# fault_injector Python 依赖安装说明

本文档用于一次性安装 `fault_injector` 涉及到的 Python 第三方模块（运行 + 测试）。

## 1) 一条命令安装

```bash
pip install click rich pydantic PyYAML httpx asyncssh ncclient kubernetes pytest pytest-asyncio
```

## 2) requirements.txt 内容参考

将下面内容保存为 `requirements.txt` 后执行 `pip install -r requirements.txt`：

```txt
click>=8.0
rich>=13.0
pydantic>=2.0
PyYAML>=6.0
httpx>=0.25
asyncssh>=2.14
ncclient>=0.6
kubernetes>=28.0
pytest>=7.0
pytest-asyncio>=0.21
```

## 3) 说明

- 上述依赖来自 `fault_injector` 与 `lib/channels` 的实际 import。
- `pytest` 与 `pytest-asyncio` 仅在执行测试时需要；若只运行功能可不安装这两项。
