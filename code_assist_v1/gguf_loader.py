"""
Standalone GGUF loading for code_assist_v1.

The loader is intentionally model-profile driven. Configured local profiles can
set GGUF path env vars, filename patterns, llama.cpp runtime knobs, and optional
vision projection/mmproj handlers.
"""
from __future__ import annotations

from fnmatch import fnmatch
import glob
import os
import subprocess
import sys

import code_assist_v1.utils as _u
from code_assist_v1.models import (
    GGUF_DEFAULT_EMBEDDING,
    GGUF_DEFAULT_FLASH_ATTN,
    GGUF_DEFAULT_LOGITS_ALL,
    GGUF_DEFAULT_MUL_MAT_Q,
    GGUF_DEFAULT_N_BATCH,
    GGUF_DEFAULT_N_CTX,
    GGUF_DEFAULT_N_GPU_LAYERS,
    GGUF_DEFAULT_N_UBATCH,
    GGUF_DEFAULT_OFFLOAD_KQV,
    GGUF_DEFAULT_USE_MLOCK,
    GGUF_DEFAULT_USE_MMAP,
)

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(_THIS_DIR)


def _unique_dirs(paths: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for path in paths:
        if not path:
            continue
        abspath = os.path.abspath(path)
        key = os.path.normcase(abspath)
        if key in seen:
            continue
        seen.add(key)
        out.append(abspath)
    return out


def _gguf_dirs() -> list[str]:
    package_dir = os.path.join(_THIS_DIR, "MODEL_GGUF")
    legacy_dirs = [
        _ROOT_DIR,
        os.path.join(_ROOT_DIR, "models"),
        os.path.join(_ROOT_DIR, "model"),
    ]
    if os.environ.get("CODE_ASSIST_STANDALONE", "").lower() in {"1", "true", "yes", "on"}:
        return _unique_dirs([package_dir, *legacy_dirs])
    return _unique_dirs(legacy_dirs + [package_dir])


def _gguf_dir() -> str:
    return _gguf_dirs()[0]


def find_gguf_files() -> list[dict]:
    """Return non-mmproj GGUF files from the configured GGUF directory."""
    from code_assist_v1.model_runtime import assert_local_inference_allowed

    assert_local_inference_allowed()
    files: list[str] = []
    for d in _gguf_dirs():
        if os.path.isdir(d):
            files.extend(glob.glob(os.path.join(d, "*.gguf")))
    files = list(dict.fromkeys(os.path.abspath(f) for f in files))
    files = [f for f in files if not _is_mmproj_name(os.path.basename(f))]
    return [_file_info(f) for f in files]


def _file_info(path: str) -> dict:
    return {
        "path": os.path.abspath(path),
        "name": os.path.basename(path),
        "size_gb": round(os.path.getsize(path) / 1e9, 1),
    }


def _is_mmproj_name(name: str) -> bool:
    name_l = name.lower()
    return "mmproj" in name_l or "projector" in name_l


def _as_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _first_existing_path(paths: list[str]) -> str | None:
    for path in paths:
        if path and os.path.isfile(path):
            return os.path.abspath(path)
    return None


def _match_file(
    patterns: list[str],
    *,
    include_mmproj: bool | None = None,
    gguf_files: list[dict] | None = None,
) -> dict | None:
    if not patterns:
        return None
    if gguf_files is None:
        paths: list[str] = []
        for d in _gguf_dirs():
            if os.path.isdir(d):
                paths.extend(glob.glob(os.path.join(d, "*.gguf")))
        gguf_files = [_file_info(path) for path in paths]
    for pattern in patterns:
        pattern_l = pattern.lower()
        for item in gguf_files:
            path = item.get("path") or ""
            name_l = os.path.basename(path).lower()
            is_mmproj = _is_mmproj_name(name_l)
            if include_mmproj is True and not is_mmproj:
                continue
            if include_mmproj is False and is_mmproj:
                continue
            if fnmatch(name_l, pattern_l):
                return item
    return None


def resolve_gguf_paths(model_config: dict, gguf_files: list[dict] | None = None) -> dict:
    """Resolve configured GGUF and optional mmproj paths for a local profile."""
    from code_assist_v1.model_runtime import assert_local_inference_allowed

    assert_local_inference_allowed()
    cfg = model_config.get("gguf") if isinstance(model_config.get("gguf"), dict) else {}
    model_env = str(cfg.get("model_path_env") or "").strip()
    mmproj_env = str(cfg.get("mmproj_path_env") or "").strip()

    model_path = _first_existing_path([os.environ.get(model_env, "")] if model_env else [])
    mmproj_path = _first_existing_path([os.environ.get(mmproj_env, "")] if mmproj_env else [])

    model_patterns = _as_list(cfg.get("model_patterns") or cfg.get("model_pattern"))
    mmproj_patterns = _as_list(cfg.get("mmproj_patterns") or cfg.get("mmproj_pattern"))
    if not model_path:
        matched = _match_file(model_patterns, include_mmproj=False, gguf_files=gguf_files)
        if matched:
            model_path = matched["path"]
    if model_path and not mmproj_path:
        matched_mmproj = _match_file(mmproj_patterns, include_mmproj=True, gguf_files=gguf_files)
        mmproj_path = matched_mmproj["path"] if matched_mmproj else None
    if model_path and not mmproj_path:
        mmproj_path = _find_mmproj_file(model_path, patterns=mmproj_patterns)

    size_gb = None
    if model_path:
        try:
            size_gb = round(os.path.getsize(model_path) / 1e9, 1)
        except OSError:
            size_gb = None
    return {
        "model_path": model_path,
        "mmproj_path": mmproj_path,
        "size_gb": size_gb,
        "patterns": model_patterns,
    }


def _find_mmproj_file(model_path: str, patterns: list[str] | None = None) -> str | None:
    """Find a vision projection file near the model or in the GGUF dir."""
    if patterns:
        matched = _match_file(patterns, include_mmproj=True)
        if matched:
            return matched["path"]
    model_dir = os.path.dirname(model_path)
    for path in glob.glob(os.path.join(model_dir, "*mmproj*.gguf")):
        return os.path.abspath(path)
    for path in glob.glob(os.path.join(model_dir, "*projector*.gguf")):
        return os.path.abspath(path)
    for d in _gguf_dirs():
        for path in glob.glob(os.path.join(d, "*mmproj*.gguf")):
            return os.path.abspath(path)
        for path in glob.glob(os.path.join(d, "*projector*.gguf")):
            return os.path.abspath(path)
    return None


def _runtime_cfg(model_config: dict | None) -> dict:
    if not isinstance(model_config, dict):
        return {}
    cfg = model_config.get("gguf")
    return cfg if isinstance(cfg, dict) else {}


def _cfg_int(cfg: dict, key: str, default: int) -> int:
    env_key = str(cfg.get(f"{key}_env") or "").strip()
    if env_key:
        env_value = os.getenv(env_key, "").strip()
        if env_value:
            try:
                return int(env_value)
            except ValueError:
                pass
    try:
        return int(cfg.get(key, default))
    except (TypeError, ValueError):
        return default


def _cfg_bool(cfg: dict, key: str, default: bool) -> bool:
    value = cfg.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)


def _cfg_float_list(cfg: dict, key: str) -> list[float]:
    env_key = str(cfg.get(f"{key}_env") or "").strip()
    value = os.getenv(env_key, "").strip() if env_key else ""
    if not value:
        value = cfg.get(key)
    if isinstance(value, list):
        raw = value
    elif isinstance(value, str):
        raw = [x.strip() for x in value.replace(";", ",").split(",")]
    else:
        return []
    out: list[float] = []
    for item in raw:
        try:
            num = float(item)
        except (TypeError, ValueError):
            continue
        if num > 0:
            out.append(num)
    return out


def _cfg_int_list(cfg: dict, key: str) -> list[int]:
    env_key = str(cfg.get(f"{key}_env") or "").strip()
    value = os.getenv(env_key, "").strip() if env_key else ""
    if not value:
        value = cfg.get(key)
    if isinstance(value, list):
        raw = value
    elif isinstance(value, str):
        raw = [x.strip() for x in value.replace(";", ",").split(",")]
    else:
        return []
    out: list[int] = []
    for item in raw:
        try:
            n = int(item)
        except (TypeError, ValueError):
            continue
        if n > 0 and n not in out:
            out.append(n)
    return out


def _ctx_candidates(cfg: dict, target: int) -> list[int]:
    configured = _cfg_int_list(cfg, "n_ctx_fallbacks")
    if configured:
        values = [target, *configured]
    else:
        values = [target, 65536, 32768, 8192]
    out: list[int] = []
    for value in values:
        try:
            n = int(value)
        except (TypeError, ValueError):
            continue
        if n > 0 and n <= target and n not in out:
            out.append(n)
    return out or [target]


def gpu_memory_snapshot() -> list[dict]:
    """Return NVIDIA GPU memory info when nvidia-smi is available."""
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.free",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return []
    if proc.returncode != 0:
        return []
    rows: list[dict] = []
    for line in proc.stdout.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 3:
            continue
        try:
            total_mb = int(parts[1])
            free_mb = int(parts[2])
        except ValueError:
            continue
        rows.append({"name": parts[0], "total_mb": total_mb, "free_mb": free_mb})
    return rows


def _format_gpu_memory(rows: list[dict]) -> str:
    if not rows:
        return "unavailable"
    parts = []
    for idx, row in enumerate(rows):
        parts.append(
            f"GPU{idx} {row['name']}: free {row['free_mb'] / 1024:.1f}GB / total {row['total_mb'] / 1024:.1f}GB"
        )
    return "; ".join(parts)


def _llama_kwargs(
    model_path: str,
    cfg: dict,
    n_ctx: int,
    n_gpu_layers: int,
    n_batch: int,
) -> dict:
    kwargs = {
        "model_path": model_path,
        "n_ctx": n_ctx,
        "n_gpu_layers": n_gpu_layers,
        "n_batch": n_batch,
        "n_ubatch": _cfg_int(cfg, "n_ubatch", GGUF_DEFAULT_N_UBATCH),
        "flash_attn": _cfg_bool(cfg, "flash_attn", GGUF_DEFAULT_FLASH_ATTN),
        "offload_kqv": _cfg_bool(cfg, "offload_kqv", GGUF_DEFAULT_OFFLOAD_KQV),
        "mul_mat_q": _cfg_bool(cfg, "mul_mat_q", GGUF_DEFAULT_MUL_MAT_Q),
        "logits_all": _cfg_bool(cfg, "logits_all", GGUF_DEFAULT_LOGITS_ALL),
        "embedding": _cfg_bool(cfg, "embedding", GGUF_DEFAULT_EMBEDDING),
        "use_mmap": _cfg_bool(cfg, "use_mmap", GGUF_DEFAULT_USE_MMAP),
        "use_mlock": _cfg_bool(cfg, "use_mlock", GGUF_DEFAULT_USE_MLOCK),
        "verbose": False,
    }
    tensor_split = _cfg_float_list(cfg, "tensor_split")
    if tensor_split:
        kwargs["tensor_split"] = tensor_split
    if cfg.get("main_gpu") is not None:
        kwargs["main_gpu"] = _cfg_int(cfg, "main_gpu", 0)
    if cfg.get("split_mode") is not None:
        kwargs["split_mode"] = _cfg_int(cfg, "split_mode", 0)
    if cfg.get("n_threads") is not None or cfg.get("n_threads_env") is not None:
        kwargs["n_threads"] = _cfg_int(cfg, "n_threads", 0)
    if cfg.get("n_threads_batch") is not None or cfg.get("n_threads_batch_env") is not None:
        kwargs["n_threads_batch"] = _cfg_int(cfg, "n_threads_batch", 0)
    for optional_bool in ("op_offload", "swa_full", "no_perf"):
        if cfg.get(optional_bool) is not None:
            kwargs[optional_bool] = _cfg_bool(cfg, optional_bool, False)
    return kwargs


def _new_llama(Llama, kwargs: dict):
    optional_keys = [
        "flash_attn",
        "offload_kqv",
        "mul_mat_q",
        "n_ubatch",
        "use_mmap",
        "use_mlock",
        "logits_all",
        "embedding",
        "n_batch",
        "tensor_split",
        "main_gpu",
        "split_mode",
        "n_threads",
        "n_threads_batch",
        "op_offload",
        "swa_full",
        "no_perf",
    ]
    current = dict(kwargs)
    while True:
        try:
            return Llama(**current)
        except TypeError:
            removed = False
            for key in optional_keys:
                if key in current:
                    current.pop(key, None)
                    removed = True
                    break
            if not removed:
                raise


def _chat_handler_classes(preferred: list[str] | None = None):
    names = preferred or ["Gemma3ChatHandler", "Llava16ChatHandler", "Llava15ChatHandler"]
    out = []
    for name in names:
        try:
            mod = __import__("llama_cpp.llama_chat_format", fromlist=[name])
            out.append((name.replace("ChatHandler", ""), getattr(mod, name)))
        except (ImportError, AttributeError):
            continue
    return out


def load_gguf_model(
    model_path: str,
    n_ctx: int = GGUF_DEFAULT_N_CTX,
    n_gpu_layers: int = GGUF_DEFAULT_N_GPU_LAYERS,
    n_batch: int = GGUF_DEFAULT_N_BATCH,
    model_config: dict | None = None,
) -> bool:
    """Load a GGUF model via llama-cpp-python."""
    from code_assist_v1.model_runtime import assert_local_inference_allowed

    assert_local_inference_allowed()
    cfg = _runtime_cfg(model_config)
    n_ctx = _cfg_int(cfg, "n_ctx", n_ctx)
    n_gpu_layers = _cfg_int(cfg, "n_gpu_layers", n_gpu_layers)
    n_batch = _cfg_int(cfg, "n_batch", n_batch)

    if not model_path or not os.path.isfile(model_path):
        print(f"     GGUF file not found: {model_path}")
        return False

    mmproj_mode = str(cfg.get("mmproj_mode") or "always").strip().lower()
    mmproj_enabled = mmproj_mode not in {"0", "false", "no", "off", "text", "text-only"}
    mmproj_path = None
    if mmproj_enabled:
        mmproj_path = model_config.get("_mmproj_path") if isinstance(model_config, dict) else None
        if not mmproj_path:
            mmproj_path = _find_mmproj_file(
                model_path,
                patterns=_as_list(cfg.get("mmproj_patterns") or cfg.get("mmproj_pattern")),
            )
    if mmproj_path and cfg.get("vision_n_ctx") is not None:
        n_ctx = min(n_ctx, _cfg_int(cfg, "vision_n_ctx", n_ctx))
    target_n_ctx = n_ctx
    ctx_candidates = _ctx_candidates(cfg, target_n_ctx)

    if _u.gguf_loaded_path == model_path and _u.gguf_model is not None:
        try:
            cur_ctx_attr = getattr(_u.gguf_model, "n_ctx", None)
            cur_ctx = cur_ctx_attr() if callable(cur_ctx_attr) else (cur_ctx_attr if cur_ctx_attr else 0)
        except Exception:
            cur_ctx = 0
        loaded_mmproj = getattr(_u, "gguf_loaded_mmproj_path", None)
        loaded_target = int(getattr(_u, "gguf_loaded_target_n_ctx", 0) or 0)
        if loaded_mmproj == mmproj_path and (
            cur_ctx >= target_n_ctx or (loaded_target == target_n_ctx and cur_ctx in ctx_candidates)
        ):
            print(f"     GGUF already loaded: {os.path.basename(model_path)} (ctx={cur_ctx})")
            return True
        print(f"     Reloading GGUF for ctx/mmproj change: {os.path.basename(model_path)}")

    try:
        from llama_cpp import Llama
    except ImportError:
        print("     llama-cpp-python is not installed")
        return False

    print(f"     Loading GGUF: {os.path.basename(model_path)}")
    if _cfg_bool(cfg, "log_gpu_memory", True):
        print(f"     GPU VRAM before GGUF load: {_format_gpu_memory(gpu_memory_snapshot())}")

    if _u.gguf_model is not None:
        print(f"     Releasing previous GGUF: {os.path.basename(_u.gguf_loaded_path or '')}")
        _u.gguf_model = None
        _u.gguf_loaded_path = None
        _u.gguf_loaded_mmproj_path = None
        _u.gguf_loaded_config = None

    saved_stdout, saved_stderr = sys.stdout, sys.stderr
    loaded_mmproj_path = None
    loaded_effective_n_ctx = 0
    last_error: Exception | None = None
    try:
        for candidate_n_ctx in ctx_candidates:
            try:
                if candidate_n_ctx != target_n_ctx:
                    print(f"     Retrying GGUF with fallback n_ctx={candidate_n_ctx} (target={target_n_ctx})")
                base_kwargs = _llama_kwargs(
                    model_path=model_path,
                    cfg=cfg,
                    n_ctx=candidate_n_ctx,
                    n_gpu_layers=n_gpu_layers,
                    n_batch=n_batch,
                )

                if mmproj_path:
                    loaded = False
                    try:
                        _u.gguf_model = _new_llama(Llama, {**base_kwargs, "clip_model_path": mmproj_path})
                        print(f"     Vision mode (clip_model_path): {os.path.basename(mmproj_path)}")
                        loaded = True
                        loaded_mmproj_path = mmproj_path
                    except Exception:
                        loaded = False
                    if not loaded:
                        for hname, HCls in _chat_handler_classes(_as_list(cfg.get("chat_handlers"))):
                            try:
                                _u.gguf_model = _new_llama(
                                    Llama,
                                    {**base_kwargs, "chat_handler": HCls(clip_model_path=mmproj_path)},
                                )
                                print(f"     Vision mode ({hname}): {os.path.basename(mmproj_path)}")
                                loaded = True
                                loaded_mmproj_path = mmproj_path
                                break
                            except Exception:
                                continue
                    if not loaded:
                        print("     Vision projection load failed; falling back to text-only GGUF")
                        _u.gguf_model = _new_llama(Llama, base_kwargs)
                else:
                    _u.gguf_model = _new_llama(Llama, base_kwargs)
                loaded_effective_n_ctx = candidate_n_ctx
                break
            except Exception as e:
                last_error = e
                _u.gguf_model = None
                continue
        if _u.gguf_model is None:
            raise last_error or RuntimeError("GGUF load failed")
    except Exception as e:
        sys.stdout, sys.stderr = saved_stdout, saved_stderr
        print(f"     GGUF load failed: {e}")
        return False
    finally:
        sys.stdout, sys.stderr = saved_stdout, saved_stderr

    _u.gguf_loaded_path = model_path
    _u.gguf_loaded_config = model_config or {}
    _u.gguf_loaded_mmproj_path = loaded_mmproj_path
    _u.gguf_loaded_target_n_ctx = target_n_ctx
    _u.gguf_loaded_effective_n_ctx = loaded_effective_n_ctx
    return True


def is_gguf_model_ready(
    model_path: str,
    n_ctx: int = GGUF_DEFAULT_N_CTX,
    n_gpu_layers: int = GGUF_DEFAULT_N_GPU_LAYERS,
    n_batch: int = GGUF_DEFAULT_N_BATCH,
    model_config: dict | None = None,
) -> bool:
    """Return True when the process-global GGUF instance already matches this request."""
    if not model_path or not os.path.isfile(model_path):
        return False
    if _u.gguf_loaded_path != model_path or _u.gguf_model is None:
        return False

    cfg = _runtime_cfg(model_config)
    n_ctx = _cfg_int(cfg, "n_ctx", n_ctx)

    mmproj_mode = str(cfg.get("mmproj_mode") or "always").strip().lower()
    mmproj_enabled = mmproj_mode not in {"0", "false", "no", "off", "text", "text-only"}
    mmproj_path = None
    if mmproj_enabled:
        mmproj_path = model_config.get("_mmproj_path") if isinstance(model_config, dict) else None
        if not mmproj_path:
            mmproj_path = _find_mmproj_file(
                model_path,
                patterns=_as_list(cfg.get("mmproj_patterns") or cfg.get("mmproj_pattern")),
            )
    if mmproj_path and cfg.get("vision_n_ctx") is not None:
        n_ctx = min(n_ctx, _cfg_int(cfg, "vision_n_ctx", n_ctx))
    target_n_ctx = n_ctx
    ctx_candidates = _ctx_candidates(cfg, target_n_ctx)

    loaded_mmproj = getattr(_u, "gguf_loaded_mmproj_path", None)
    if loaded_mmproj != mmproj_path:
        return False
    try:
        cur_ctx_attr = getattr(_u.gguf_model, "n_ctx", None)
        cur_ctx = cur_ctx_attr() if callable(cur_ctx_attr) else (cur_ctx_attr if cur_ctx_attr else 0)
    except Exception:
        cur_ctx = 0
    loaded_target = int(getattr(_u, "gguf_loaded_target_n_ctx", 0) or 0)
    return cur_ctx >= target_n_ctx or (loaded_target == target_n_ctx and cur_ctx in ctx_candidates)


def loaded_gguf_n_ctx() -> int:
    """Return the effective n_ctx of the currently loaded GGUF model."""
    try:
        cur_ctx_attr = getattr(_u.gguf_model, "n_ctx", None)
        cur_ctx = cur_ctx_attr() if callable(cur_ctx_attr) else (cur_ctx_attr if cur_ctx_attr else 0)
    except Exception:
        cur_ctx = 0
    return int(cur_ctx or getattr(_u, "gguf_loaded_effective_n_ctx", 0) or 0)


def gguf_chat_stream(messages: list[dict], temperature: float = 0.5, max_tokens: int = 4096):
    """Stream chat text from the loaded GGUF model."""
    from code_assist_v1.model_runtime import assert_local_inference_allowed

    assert_local_inference_allowed()
    if _u.gguf_model is None:
        raise RuntimeError("GGUF model is not loaded")
    for chunk in _u.gguf_model.create_chat_completion(
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        stream=True,
    ):
        choices = chunk.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta") or {}
        content = delta.get("content") or ""
        if content:
            yield content
