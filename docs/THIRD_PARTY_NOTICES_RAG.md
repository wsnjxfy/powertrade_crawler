# RAG 第三方组件与许可

Powertrade Crawler 的本地知识库分发包包含下列第三方组件。它们只用于本机 CPU
向量推理，不会把语料或查询发送到在线 Embedding API。

| 组件 | 用途 | 许可 |
| --- | --- | --- |
| BAAI/bge-small-zh-v1.5 | 512 维中文文本向量模型 | MIT |
| FastEmbed 0.8.0 | ONNX 文本向量推理封装 | Apache-2.0 |
| ONNX Runtime 1.28.0 | CPU 推理运行时 | MIT |
| Hugging Face Tokenizers | 本地分词运行时 | Apache-2.0 |

组件包内的许可元数据会由 PyInstaller 一并收集；本说明不替代各上游项目的许可文本。

- BGE 模型：<https://huggingface.co/BAAI/bge-small-zh-v1.5>
- FastEmbed：<https://github.com/qdrant/fastembed>
- ONNX Runtime：<https://github.com/microsoft/onnxruntime>
- Tokenizers：<https://github.com/huggingface/tokenizers>
