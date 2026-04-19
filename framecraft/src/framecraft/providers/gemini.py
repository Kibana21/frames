"""Gemini adapter — the default FrameCraft provider.

Implements the `LLMProvider` protocol with `cachedContents` lifecycle
management. See `.claude/plans/02-providers.md` §3.

Cache mechanics:
  - A stable prefix (primer + registry + schema) is passed as `cache_segments`.
  - We hash it and keep a local index at `<user_cache>/framecraft/gemini-caches.json`
    mapping hash → (cache_name, expires_at).
  - First call: create a `cachedContents` entry server-side (1h TTL), record it.
  - Subsequent calls: reuse the cache_name, reducing per-call input tokens
    dramatically (cached_content_token_count > 0 in the response).
  - Entries expiring in <2 minutes are recreated proactively to avoid
    mid-run expiry within a single `compose`.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from pathlib import Path
from typing import Any

from platformdirs import user_cache_dir

from framecraft.providers.base import (
    LLMProvider,
    Message,
    ProviderAuthError,
    ProviderNetworkError,
    ProviderRateLimitError,
    ProviderResponse,
    cache_key,
    default_model,
)
from framecraft.schema import SCHEMA_HASH

_CACHE_DIR = Path(user_cache_dir("framecraft"))


def _normalize_schema_for_vertexai(schema: dict) -> dict:
    """Translate a Pydantic JSON schema into a Vertex AI–compatible schema dict.

    Vertex AI accepts only a subset of JSON Schema. This function:
      - Resolves all $ref / $defs
      - Unwraps anyOf/oneOf with a null branch (Optional[X] → X)
      - Converts prefixItems (tuple) → items with the common type
      - Converts const to a plain type annotation
      - Converts non-string enums (Literal[N]) to plain type
      - Strips keys Vertex AI rejects (additionalProperties, exclusiveMinimum, …)
    """
    defs = schema.get("$defs", {})
    return _normalize_node(schema, defs)


# Keys Vertex AI's Schema proto accepts.
_VERTEXAI_ALLOWED = frozenset({
    "type", "properties", "items", "enum", "description", "nullable",
    "required", "format", "minimum", "maximum", "minItems", "maxItems",
    "minLength", "maxLength", "pattern",
})


def _normalize_node(node: Any, defs: dict) -> Any:
    if not isinstance(node, dict):
        return node

    # Resolve $ref first
    if "$ref" in node:
        ref_name = node["$ref"].split("/")[-1]
        resolved = dict(defs.get(ref_name, {}))
        # Merge any sibling keys (e.g. description) onto the resolved node
        for k, v in node.items():
            if k != "$ref" and k not in resolved:
                resolved[k] = v
        return _normalize_node(resolved, defs)

    # Unwrap anyOf/oneOf: pick the non-null branch for Optional[X]
    for key in ("anyOf", "oneOf"):
        if key in node:
            branches = node[key]
            non_null = [b for b in branches if b.get("type") != "null"]
            outer = {k: v for k, v in node.items() if k != key}
            if len(non_null) == 1:
                merged = _normalize_node(dict(non_null[0]), defs)
                for mk, mv in outer.items():
                    if mk not in merged:
                        merged[mk] = mv
                return _normalize_node(merged, defs)
            if non_null:
                merged = _normalize_node(dict(non_null[0]), defs)
                for mk, mv in outer.items():
                    if mk not in merged:
                        merged[mk] = mv
                return _normalize_node(merged, defs)

    # Convert const → plain type (Pydantic uses const for Literal[N])
    if "const" in node:
        const_val = node["const"]
        type_name = (
            "boolean" if isinstance(const_val, bool) else
            "integer" if isinstance(const_val, int) else
            "number"  if isinstance(const_val, float) else
            "string"
        )
        result: dict = {k: v for k, v in node.items() if k != "const"}
        result.setdefault("type", type_name)
        return _normalize_node(result, defs)

    # Convert prefixItems (tuple) → items with the first branch type
    if "prefixItems" in node:
        prefix = node["prefixItems"]
        first = _normalize_node(dict(prefix[0]) if prefix else {}, defs)
        result = {k: v for k, v in node.items() if k != "prefixItems"}
        result["items"] = first
        result.setdefault("type", "array")
        return _normalize_node(result, defs)

    # Recurse into properties
    props = node.get("properties")
    items = node.get("items")
    result = {}

    for k, v in node.items():
        if k == "properties" and props is not None:
            result["properties"] = {pk: _normalize_node(pv, defs) for pk, pv in props.items()}
        elif k == "items" and items is not None:
            result["items"] = _normalize_node(items, defs)
        elif k in _VERTEXAI_ALLOWED:
            result[k] = v
        # else: drop the key (unsupported by Vertex AI)

    # Fix enum: non-string values → plain type, missing type → "string"
    if "enum" in result:
        vals = result["enum"]
        if all(isinstance(v, str) for v in vals):
            result.setdefault("type", "string")
        else:
            result.pop("enum")
            if not all(isinstance(v, bool) for v in vals):
                result.setdefault("type", "integer" if all(isinstance(v, int) for v in vals) else "string")

    # Infer missing type annotations
    if "properties" in result and "type" not in result:
        result["type"] = "object"
    if "items" in result and "type" not in result:
        result["type"] = "array"

    return result


def _find_key_json() -> Path | None:
    """Search for key.json in several locations.

    Order:
      1. CWD and its parents up to home (e.g. running from the project root).
      2. Immediate subdirectories of CWD (e.g. running from a workspace that
         contains a framecraft/ subfolder with the key inside).
      3. ~/.config/framecraft/key.json (standard config location).
    """
    home = Path.home()
    cwd = Path.cwd()

    # 1. Walk up from CWD.
    current = cwd
    while True:
        candidate = current / "key.json"
        if candidate.exists():
            return candidate
        if current == home or current == current.parent:
            break
        current = current.parent

    # 2. One level down — immediate subdirectories of CWD.
    try:
        for child in sorted(cwd.iterdir()):
            if child.is_dir():
                candidate = child / "key.json"
                if candidate.exists():
                    return candidate
    except PermissionError:
        pass

    # 3. Standard config location.
    config_key = home / ".config" / "framecraft" / "key.json"
    if config_key.exists():
        return config_key

    return None
_CACHE_INDEX = _CACHE_DIR / "gemini-caches.json"
_TTL_SECONDS = 3600  # 1 hour — Gemini's default; the adapter requests the same
_RENEW_WINDOW_SECONDS = 120  # recreate when expiry is within this many seconds
_MIN_TOKENS_FOR_CACHE = 1024  # Gemini rejects tiny cached contents


class GeminiProvider:
    """Default FrameCraft provider.

    Auth priority (first match wins):
      1. `key_file` constructor argument
      2. `GOOGLE_APPLICATION_CREDENTIALS` env var (standard ADC path)
      3. `key.json` in the current working directory
      4. `GEMINI_API_KEY` / `GOOGLE_API_KEY` env vars
    """

    name: str = "gemini"

    _SA_SCOPES = ["https://www.googleapis.com/auth/generative-language"]

    def __init__(self, key_file: Path | None = None) -> None:
        # Lazy imports so `import framecraft.providers` doesn't require the SDK.
        try:
            from google import genai
            from google.genai import types
        except ImportError as e:  # pragma: no cover - install-time check
            raise ProviderAuthError(
                "google-genai is not installed. Run: pip install google-genai"
            ) from e

        self._genai = genai
        self._types = types
        self._vertexai = False  # set to True by _build_client when SA is used
        self._client = self._build_client(genai, key_file)

    def _build_client(self, genai: Any, key_file: Path | None) -> Any:
        # 1. Explicit key file passed in code.
        sa_path = key_file
        # 2. Standard ADC env var.
        if sa_path is None and os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
            sa_path = Path(os.environ["GOOGLE_APPLICATION_CREDENTIALS"])
        # 3. key.json next to CWD (developer convenience).
        if sa_path is None:
            sa_path = _find_key_json()

        if sa_path is not None:
            try:
                from google.oauth2 import service_account
                # Service account credentials require Vertex AI mode in google-genai.
                # Parse project from the key file; allow override via env.
                import json as _json
                sa_data = _json.loads(Path(sa_path).read_text(encoding="utf-8"))
                project = (
                    os.environ.get("GOOGLE_CLOUD_PROJECT")
                    or sa_data.get("project_id")
                )
                location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
                creds = service_account.Credentials.from_service_account_info(
                    sa_data,
                    scopes=["https://www.googleapis.com/auth/cloud-platform"],
                )
                self._vertexai = True
                return genai.Client(
                    vertexai=True,
                    project=project,
                    location=location,
                    credentials=creds,
                )
            except ProviderAuthError:
                raise
            except Exception as e:
                raise ProviderAuthError(
                    f"Failed to load service account from {sa_path}: {e}"
                ) from e

        # 4. Fall back to API key.
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise ProviderAuthError(
                "No Gemini credentials found. Provide one of:\n"
                "  • key.json (service account) in the working directory\n"
                "  • GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json\n"
                "  • GEMINI_API_KEY or GOOGLE_API_KEY env var"
            )
        return genai.Client(api_key=api_key)

    # --- public protocol ---------------------------------------------------

    def complete(
        self,
        messages: list[Message],
        *,
        system: str,
        schema: dict | None = None,
        cache_segments: list[str] | None = None,
        model: str | None = None,
    ) -> ProviderResponse:
        t0 = time.perf_counter()
        chosen_model = model or default_model("director", self.name)

        cache_name = self._ensure_cache(chosen_model, cache_segments, system)

        contents = self._messages_to_contents(messages)
        config = self._build_generate_config(
            system=system,
            schema=schema,
            cache_name=cache_name,
        )
        try:
            response = self._client.models.generate_content(
                model=chosen_model,
                contents=contents,
                config=config,
            )
        except Exception as e:  # SDK raises a family of errors; map broadly
            self._raise_mapped(e)

        text = response.text or ""
        parsed: dict | None = None
        if schema is not None:
            parsed = self._parse_json(text)

        usage = getattr(response, "usage_metadata", None)
        input_tokens = getattr(usage, "prompt_token_count", 0) or 0
        output_tokens = getattr(usage, "candidates_token_count", 0) or 0
        cache_read_tokens = getattr(usage, "cached_content_token_count", 0) or 0

        return ProviderResponse(
            text=text,
            parsed=parsed,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=0,  # Gemini doesn't expose write tokens; creation is a separate RPC
            provider=self.name,
            model=chosen_model,
            elapsed_ms=int((time.perf_counter() - t0) * 1000),
        )

    # --- cache lifecycle ---------------------------------------------------

    def _ensure_cache(
        self,
        model: str,
        cache_segments: list[str] | None,
        system: str,
    ) -> str | None:
        """Return a cache name usable in generate_content, or None if no cache."""
        if not cache_segments:
            return None
        # Check Gemini's minimum: concatenated segments must exceed ~1024 tokens.
        # We don't tokenize; use a rough 4-chars-per-token heuristic.
        if sum(len(seg) for seg in cache_segments) < _MIN_TOKENS_FOR_CACHE * 4:
            return None

        key = cache_key(cache_segments, model, SCHEMA_HASH)
        entry = self._lookup_cache_entry(key)
        if entry is not None:
            cache_name, expires_at = entry
            if expires_at - time.time() > _RENEW_WINDOW_SECONDS:
                return cache_name
            # Expiring soon — fall through to recreate.

        cache_name = self._create_cache(model, cache_segments, system)
        self._persist_cache_entry(key, cache_name, time.time() + _TTL_SECONDS - 60)
        return cache_name

    def _create_cache(
        self,
        model: str,
        cache_segments: list[str],
        system: str,
    ) -> str:
        types = self._types
        parts = [types.Part.from_text(text=seg) for seg in cache_segments]
        contents = [types.Content(role="user", parts=parts)]
        try:
            cached = self._client.caches.create(
                model=model,
                config=types.CreateCachedContentConfig(
                    contents=contents,
                    system_instruction=system,
                    ttl=f"{_TTL_SECONDS}s",
                ),
            )
        except Exception as e:
            self._raise_mapped(e)
        return cached.name

    def _lookup_cache_entry(self, key: str) -> tuple[str, float] | None:
        if not _CACHE_INDEX.exists():
            return None
        try:
            data = self._read_index()
        except Exception:
            return None
        entry = data.get(key)
        if not entry:
            return None
        return entry["name"], float(entry["expires_at"])

    def _persist_cache_entry(self, key: str, name: str, expires_at: float) -> None:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        # Open with exclusive flock so concurrent compose runs don't clobber.
        with open(_CACHE_INDEX, "a+", encoding="utf-8") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                fh.seek(0)
                raw = fh.read()
                data: dict[str, Any] = json.loads(raw) if raw.strip() else {}
                data[key] = {"name": name, "expires_at": expires_at}
                # Prune expired entries opportunistically.
                now = time.time()
                data = {k: v for k, v in data.items() if v.get("expires_at", 0) > now}
                fh.seek(0)
                fh.truncate()
                fh.write(json.dumps(data, indent=2))
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    def _read_index(self) -> dict[str, Any]:
        with open(_CACHE_INDEX, "r", encoding="utf-8") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_SH)
            try:
                raw = fh.read()
                return json.loads(raw) if raw.strip() else {}
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    # --- internals ---------------------------------------------------------

    def _messages_to_contents(self, messages: list[Message]) -> list[Any]:
        types = self._types
        out: list[Any] = []
        for m in messages:
            role = "user" if m["role"] == "user" else "model"
            out.append(types.Content(role=role, parts=[types.Part.from_text(text=m["content"])]))
        return out

    def _build_generate_config(
        self,
        *,
        system: str,
        schema: dict | None,
        cache_name: str | None,
    ) -> Any:
        types = self._types
        kwargs: dict[str, Any] = {"temperature": 0}
        if cache_name is not None:
            kwargs["cached_content"] = cache_name
        else:
            # When no cache is used, the system instruction still needs to be set.
            kwargs["system_instruction"] = system
        if schema is not None:
            kwargs["response_mime_type"] = "application/json"
            if not self._vertexai:
                # Standard Gemini API: constrain structure with response_schema.
                kwargs["response_schema"] = schema
            else:
                # Vertex AI: JSON mode only — no response_schema.
                # The schema contains dynamic keys (block_props: dict[str, Any])
                # that Vertex AI's strict schema enforcement leaves empty.
                # The Director/Assembler system prompts describe the format
                # explicitly, so the model produces valid JSON without schema help.
                pass
        return types.GenerateContentConfig(**kwargs)

    @staticmethod
    def _parse_json(text: str) -> dict:
        # Gemini with response_mime_type=application/json returns clean JSON,
        # but defense against occasional code-fence regressions is cheap.
        stripped = text.strip()
        if stripped.startswith("```"):
            # strip first ```... line and trailing ```
            lines = stripped.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            stripped = "\n".join(lines).strip()
        return json.loads(stripped)

    @staticmethod
    def _raise_mapped(e: Exception) -> None:
        msg = str(e)
        low = msg.lower()
        if "api key" in low or "unauthenticated" in low or "permission" in low or "401" in msg or "403" in msg:
            raise ProviderAuthError(f"Gemini auth error: {msg}") from e
        if "429" in msg or "rate" in low or "quota" in low or "resource_exhausted" in low:
            raise ProviderRateLimitError(f"Gemini rate-limited: {msg}") from e
        if "timeout" in low or "connection" in low or "network" in low:
            raise ProviderNetworkError(f"Gemini network error: {msg}") from e
        # Fall through — let the SDK's own exception propagate with context.
        raise
