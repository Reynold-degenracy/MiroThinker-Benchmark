# GAIA Benchmark 测试快速指南

## 🎯 功能说明

这个测试套件从 GAIA benchmark 中抽取测试用例，通过 API 调用来评估 MiroFlow Agent 的能力。

**GAIA 数据统计:**
- 总测试用例: 165
- Level 1 (简单): 53
- Level 2 (中等): 86  
- Level 3 (困难): 26
- 纯文本用例: 127

## 🚀 快速开始

### 1. 启动 API 服务器

```bash
cd apps/miroflow-agent
python3 api_server.py
```

### 2. 运行测试

**方法一：使用 Bash 脚本（最简单）**

```bash
# 在 MiroThinker 根目录运行
./test_gaia_benchmark.sh
```

**方法二：使用 Python 脚本（更多选项）**

```bash
# 安装依赖
pip3 install aiohttp

# 运行测试
python3 test_gaia_benchmark.py --num-tests 5 --level 1
```

## 📋 常用命令

```bash
# 测试 3 个简单问题 (Level 1)
python3 test_gaia_benchmark.py --num-tests 3 --level 1

# 测试 5 个中等难度问题 (Level 2)  
python3 test_gaia_benchmark.py --num-tests 5 --level 2

# 测试 10 个任意难度的问题
python3 test_gaia_benchmark.py --num-tests 10

# 使用固定随机种子（可重复）
python3 test_gaia_benchmark.py --num-tests 5 --seed 42

# 指定不同的 API 地址
python3 test_gaia_benchmark.py --api-url http://192.168.1.100:8000
```

## 📊 输出文件

- `gaia_test_results.json` - 详细测试结果（JSON格式）
- `gaia_test_results.log` - 运行日志

## 📁 文件列表

- `test_gaia_benchmark.py` - 主测试脚本
- `test_gaia_benchmark.sh` - 快速启动脚本  
- `validate_setup.py` - 验证数据和环境
- `GAIA_TEST_README.md` - 完整文档
- `QUICKSTART.md` - 本文件

## ⚙️ 环境变量

```bash
# 通过环境变量配置（bash脚本）
NUM_TESTS=5 LEVEL=2 API_URL=http://localhost:8000 ./test_gaia_benchmark.sh
```

## 🔍 结果示例

```
==========================================
TEST SUMMARY
==========================================
Total Tests: 5
Successful Responses: 5 (100.0%)
Exact Matches: 2 (40.0%)
Contains Matches: 4 (80.0%)
Average Response Time: 45.30s
Average Plan Steps: 8.2
==========================================
```

## ❓ 故障排除

**API 无法连接:**
```bash
# 检查 API 是否运行
curl http://localhost:8000/health
```

**找不到数据文件:**
```bash
# 验证数据文件
python3 validate_setup.py
```

**缺少依赖:**
```bash
pip3 install aiohttp
```

## 📖 更多信息

查看完整文档: [GAIA_TEST_README.md](GAIA_TEST_README.md)
