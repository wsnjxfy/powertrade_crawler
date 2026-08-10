from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from powertrade_crawler.market_agent.rag import RAG_MODEL_ID


MANIFEST_NAME = "powertrade-rag-model-manifest.json"


def file_manifest(model_path: Path) -> list[dict[str, object]]:
    files = []
    for path in sorted(model_path.rglob("*")):
        if not path.is_file() or path.name == MANIFEST_NAME:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        files.append(
            {
                "path": path.relative_to(model_path).as_posix(),
                "size": path.stat().st_size,
                "sha256": digest,
            }
        )
    return files


def verify(model_path: Path) -> dict[str, object]:
    manifest_path = model_path / MANIFEST_NAME
    if not manifest_path.is_file():
        raise RuntimeError(f"模型清单不存在：{manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("model_id") != RAG_MODEL_ID:
        raise RuntimeError("模型清单中的 model_id 与项目配置不一致。")
    actual = file_manifest(model_path)
    expected = manifest.get("files")
    if actual != expected:
        raise RuntimeError("模型文件清单或 SHA-256 校验不一致。")
    return manifest


def prepare(model_path: Path) -> dict[str, object]:
    try:
        from fastembed import TextEmbedding
    except ImportError as exc:
        raise RuntimeError("请先安装项目依赖，再准备 RAG 模型。") from exc

    staging = model_path.with_name(f"{model_path.name}.staging")
    download_cache = model_path.with_name(f".{model_path.name}.download")
    if staging.exists():
        shutil.rmtree(staging)
    if download_cache.exists():
        shutil.rmtree(download_cache)
    staging.mkdir(parents=True)
    try:
        embedding = TextEmbedding(
            model_name=RAG_MODEL_ID,
            cache_dir=str(download_cache),
            lazy_load=True,
            local_files_only=False,
        )
        downloaded = Path(embedding.model._model_dir)  # noqa: SLF001
        if not downloaded.is_dir():
            raise RuntimeError("FastEmbed 未返回有效的模型目录。")
        shutil.copytree(downloaded, staging, dirs_exist_ok=True)
        if not (staging / "model_optimized.onnx").is_file():
            raise RuntimeError("下载结果不包含 model_optimized.onnx。")
        manifest = {
            "schema_version": 1,
            "model_id": RAG_MODEL_ID,
            "embedding_dimension": 512,
            "runtime": "fastembed==0.8.0",
            "license": "MIT",
            "files": file_manifest(staging),
        }
        (staging / MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        verify(staging)
        if model_path.exists():
            shutil.rmtree(model_path)
        staging.replace(model_path)
        shutil.rmtree(download_cache, ignore_errors=True)
        return verify(model_path)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        shutil.rmtree(download_cache, ignore_errors=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(
        description="下载并校验 Powertrade Crawler 的离线 BGE 向量模型。"
    )
    parser.add_argument("--output", type=Path, default=Path("work/rag-model"))
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    model_path = args.output.resolve()
    manifest = verify(model_path) if args.verify_only else prepare(model_path)
    total = sum(int(item["size"]) for item in manifest["files"])
    print(
        json.dumps(
            {
                "ok": True,
                "model_path": str(model_path),
                "file_count": len(manifest["files"]),
                "size_bytes": total,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
