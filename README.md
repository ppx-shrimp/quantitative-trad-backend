# Quantitative Trad Backend（A 股量化交易模拟系统后端）

Quantitative Trad Backend 是一个面向 A 股市场的量化交易模拟系统后端，提供股票基础数据、K 线同步、技术特征计算、股票池管理、模拟交易、回测、AI 交易分析、AI 观察池、RAG 新闻检索增强和系统任务记录等能力。项目基于 FastAPI、SQLAlchemy、AkShare、APScheduler、Redis、MySQL、Qdrant 以及 OpenAI-compatible LLM/Embedding 接口构建。

本项目只做模拟交易和研究分析，不包含真实券商下单能力。默认配置中 `QUANT_ALLOW_LIVE_TRADING=false`，请不要将本项目直接用于真实资金交易。

## 项目定位

这个后端适合用于个人量化研究、A 股交易策略原型验证、纸面交易模拟、AI 辅助交易分析、新闻 RAG 实验和全栈量化系统学习。它不是投资建议系统，也不是自动实盘交易系统。系统输出的分析、预测、回测和 AI 结论只用于学习、研究和辅助决策，不能替代独立判断。

## 主要功能

后端能力覆盖从数据准备到交易模拟的完整闭环。数据层支持股票基础信息、K 线、本地 K 线查询、新闻资讯同步和市场指数数据。策略层支持技术指标计算、开盘预测策略、严格/普通/宽松三种策略模式。交易层支持模拟账户、持仓、订单、资金流水、手动开仓、手动平仓、自动买入、自动平仓、盈亏统计和每日复盘。回测层支持单股回测、股票池回测、参数网格优化、策略对比、回测榜单和回测报告导出。AI 层支持单股 AI 分析、AI 推荐、AI 观察池扫描、观察池跟踪、AI 分析记录、分析复盘、多轮追问、LLM 运行诊断、RAG 运行诊断和一键 RAG 预处理。系统层支持任务执行记录、定时任务、缓存预热和数据就绪检查。

## 技术栈

后端语言是 Python 3.10+。Web 框架使用 FastAPI，ASGI 服务使用 Uvicorn。ORM 使用 SQLAlchemy 2.x，数据库迁移使用 Alembic。生产数据库推荐 MySQL 8，开发环境可使用 SQLite。行情和资讯数据主要通过 AkShare/EastMoney 获取。任务调度使用 APScheduler。缓存使用 Redis。RAG 向量库使用 Qdrant。AI 模型和 Embedding 使用 OpenAI-compatible API，可接入 DeepSeek、DashScope 或其他兼容服务。

## 目录结构

```text
.
├── alembic/                       # Alembic 迁移脚本
├── scripts/                       # 数据同步、诊断、验证和维护脚本
├── sql/                           # MySQL 建表 SQL 和数据库说明
├── src/quant_system/
│   ├── ai/                        # AI 分析、LLM、工作流、观察池、评估、运行诊断
│   ├── api/                       # FastAPI 路由、Pydantic schema、分页工具
│   ├── brokers/                   # 模拟交易 broker 抽象和 SQLite/MySQL 实现
│   ├── core/                      # 配置项
│   ├── data/                      # AkShare/EastMoney 数据源适配
│   ├── db/                        # SQLAlchemy Base、Session、ORM model
│   ├── domain/                    # 领域模型
│   ├── jobs/                      # APScheduler 定时任务
│   ├── rag/                       # 新闻 chunk、embedding、Qdrant、RAG 检索
│   ├── runtime/                   # 启动自检
│   ├── services/                  # 业务服务层
│   └── strategies/                # 策略抽象和开盘预测策略
├── tests/                         # 测试用例
├── pyproject.toml                 # Python 项目配置
├── alembic.ini                    # Alembic 配置
├── .env.example                   # 环境变量模板，禁止提交真实 .env
├── API.postman_collection.json    # Postman 可导入接口文档
├── openapi.json                   # OpenAPI 风格接口摘要
└── README.md
```

## 第一步：克隆项目

```bash
git clone https://github.com/ppx-shrimp/quantitative-trad-backend.git
cd quantitative-trad-backend
```

如果你还没有创建 GitHub 仓库，可以先在 GitHub 新建一个空仓库，然后本地执行：

```bash
git init
git add .
git commit -m "Initial open source release"
git branch -M main
git remote add origin https://github.com/ppx-shrimp/quantitative-trad-backend.git
git push -u origin main
```

## 第二步：准备 Python 环境

推荐使用 Python 3.11。

```bash
python --version
python -m venv .venv
```

Windows PowerShell：

```powershell
.\.venv\Scripts\Activate.ps1
```

macOS/Linux：

```bash
source .venv/bin/activate
```

安装依赖：

```bash
pip install --upgrade pip
pip install -e .[dev]
```

如果你在国内网络环境下安装较慢，可以使用镜像源：

```bash
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -e .[dev]
```

## 第三步：创建环境变量文件

复制模板：

```bash
cp .env.example .env
```

Windows PowerShell：

```powershell
Copy-Item .env.example .env
```

最小本地开发配置可以使用 SQLite，不需要 MySQL、Redis、Qdrant 和真实 AI Key：

```env
QUANT_ENVIRONMENT=local
QUANT_DATABASE_BACKEND=sqlite
QUANT_DATABASE_PATH=data/quant_system.db
QUANT_AI_ENABLED=true
QUANT_AI_MOCK_ENABLED=true
QUANT_RAG_ENABLED=false
QUANT_ALLOW_LIVE_TRADING=false
```

生产或完整体验建议使用 MySQL、Redis、Qdrant 和真实 LLM/Embedding：

```env
QUANT_ENVIRONMENT=production
QUANT_DATABASE_BACKEND=mysql
QUANT_DATABASE_URL=mysql+pymysql://quant:your_password@localhost:3306/quantitative_trad?charset=utf8mb4
QUANT_REDIS_ENABLED=true
QUANT_REDIS_URL=redis://localhost:6379/0
QUANT_RAG_ENABLED=true
QUANT_RAG_VECTOR_BACKEND=qdrant
QUANT_RAG_QDRANT_URL=http://localhost:6333
QUANT_RAG_COLLECTION_NEWS=market_news_chunks
QUANT_AI_ENABLED=true
QUANT_AI_MOCK_ENABLED=false
QUANT_LLM_BASE_URL=https://api.deepseek.com/v1
QUANT_LLM_API_KEY=your_llm_key
QUANT_LLM_MODEL=deepseek-chat
QUANT_RAG_EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QUANT_RAG_EMBEDDING_API_KEY=your_embedding_key
QUANT_RAG_EMBEDDING_MODEL=text-embedding-v4
QUANT_RAG_EMBEDDING_DIMENSION=1024
QUANT_RAG_EMBEDDING_BATCH_SIZE=10
```

注意：`.env` 中可能包含数据库密码和 API Key，绝对不要提交到 GitHub。

## 第四步：初始化数据库

### SQLite 本地开发

SQLite 模式下，应用启动时会自动创建基础表。你也可以直接启动服务，让系统自动初始化。

```bash
python -m uvicorn quant_system.main:app --reload
```

### MySQL 生产或完整环境

先创建数据库用户和数据库，或直接执行 `sql/mysql_schema.sql`：

```bash
mysql -uroot -p < sql/mysql_schema.sql
```

如果你使用 Alembic 管理迁移：

```bash
alembic upgrade head
```

推荐生产环境使用 Alembic，`sql/mysql_schema.sql` 更适合作为全量建表参考或首次导入脚本。

## 第五步：启动后端服务

开发模式：

```bash
python -m uvicorn quant_system.main:app --reload --host 0.0.0.0 --port 8000
```

访问健康检查：

```bash
curl http://localhost:8000/health
curl http://localhost:8000/api/v1/system/health
curl http://localhost:8000/api/v1/system/status
```

接口文档：

```text
http://localhost:8000/docs
http://localhost:8000/redoc
```

## 第六步：同步股票基础信息

首次运行系统后，股票列表通常为空，需要同步基础数据：

```bash
python scripts/sync_stock_basic.py
```

同步完成后检查：

```bash
curl "http://localhost:8000/api/v1/stocks?page=1&page_size=20"
```

## 第七步：创建或维护股票池

系统内置股票池概念，例如 `favorites`、`candidates`、`watchlist`。你可以通过接口添加股票池成员：

```bash
curl -X POST http://localhost:8000/api/v1/pools/favorites/stocks \
  -H "Content-Type: application/json" \
  -d '{"symbol":"600519","name":"贵州茅台","reason":"示例自选股","tags":["白酒","蓝筹"]}'
```

查询股票池：

```bash
curl http://localhost:8000/api/v1/pools
curl http://localhost:8000/api/v1/pools/favorites/stocks
```

## 第八步：同步 K 线数据

同步单只股票：

```bash
python scripts/sync_pool_klines.py --pool favorites --period daily
```

或者使用接口：

```bash
curl -X POST http://localhost:8000/api/v1/stocks/600519/klines/sync \
  -H "Content-Type: application/json" \
  -d '{"period":"daily"}'
```

查询本地 K 线：

```bash
curl "http://localhost:8000/api/v1/stocks/600519/klines/local?period=daily&page=1&page_size=200"
```

## 第九步：计算技术特征

单股计算：

```bash
curl -X POST http://localhost:8000/api/v1/stocks/600519/features/compute \
  -H "Content-Type: application/json" \
  -d '{"period":"daily"}'
```

股票池计算：

```bash
curl -X POST http://localhost:8000/api/v1/pools/favorites/features/compute \
  -H "Content-Type: application/json" \
  -d '{"period":"daily"}'
```

查询最新特征：

```bash
curl http://localhost:8000/api/v1/stocks/600519/features/latest
```

## 第十步：运行分析、预测和模拟交易

股票技术分析：

```bash
curl http://localhost:8000/api/v1/stocks/600519/analysis
```

股票预测：

```bash
curl http://localhost:8000/api/v1/stocks/600519/prediction
```

查询账户：

```bash
curl http://localhost:8000/api/v1/trading/account
```

手动开仓：

```bash
curl -X POST http://localhost:8000/api/v1/trading/open-position \
  -H "Content-Type: application/json" \
  -d '{"symbol":"600519","quantity":100,"price":1500}'
```

手动平仓：

```bash
curl -X POST http://localhost:8000/api/v1/trading/close-position \
  -H "Content-Type: application/json" \
  -d '{"symbol":"600519","quantity":100,"price":1520}'
```

查看持仓和订单：

```bash
curl http://localhost:8000/api/v1/trading/positions
curl http://localhost:8000/api/v1/trading/orders
curl http://localhost:8000/api/v1/trading/pnl
```

## 第十一步：运行回测

单股回测：

```bash
curl -X POST http://localhost:8000/api/v1/backtest/run \
  -H "Content-Type: application/json" \
  -d '{"symbol":"600519","period":"daily","strategy_mode":"normal","initial_cash":1000000,"quantity":100}'
```

股票池回测：

```bash
curl -X POST http://localhost:8000/api/v1/backtest/run \
  -H "Content-Type: application/json" \
  -d '{"pool_code":"favorites","period":"daily","strategy_mode":"normal","initial_cash":1000000,"quantity":100}'
```

策略对比：

```bash
curl -X POST http://localhost:8000/api/v1/backtest/strategy-compare \
  -H "Content-Type: application/json" \
  -d '{"symbol":"600519","period":"daily","strategies":["strict","normal","loose"]}'
```

回测记录：

```bash
curl http://localhost:8000/api/v1/backtest/runs
curl http://localhost:8000/api/v1/backtest/leaderboard
```

## 第十二步：启用 AI 分析

如果没有配置真实 LLM，系统会使用 Mock 模式，适合演示页面和开发联调。如果要启用真实 AI 分析，需要配置：

```env
QUANT_AI_ENABLED=true
QUANT_AI_MOCK_ENABLED=false
QUANT_LLM_BASE_URL=https://api.deepseek.com/v1
QUANT_LLM_API_KEY=your_key
QUANT_LLM_MODEL=deepseek-chat
```

检查 LLM 状态：

```bash
curl http://localhost:8000/api/v1/ai/llm/status
curl -X POST http://localhost:8000/api/v1/ai/llm/diagnose
```

发起 AI 个股分析：

```bash
curl -X POST http://localhost:8000/api/v1/ai/analyze-stock \
  -H "Content-Type: application/json" \
  -d '{"symbol":"600519","analysis_type":"trade_decision","user_question":"现在适合买入吗？"}'
```

查询 AI 分析记录：

```bash
curl "http://localhost:8000/api/v1/ai/analysis-records?symbol=600519&include_payload=true"
```

## 第十三步：启用 RAG 新闻增强

RAG 需要 Qdrant、Embedding API 和新闻数据。先启动 Qdrant，然后配置：

```env
QUANT_RAG_ENABLED=true
QUANT_RAG_VECTOR_BACKEND=qdrant
QUANT_RAG_QDRANT_URL=http://localhost:6333
QUANT_RAG_COLLECTION_NEWS=market_news_chunks
QUANT_RAG_EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QUANT_RAG_EMBEDDING_API_KEY=your_key
QUANT_RAG_EMBEDDING_MODEL=text-embedding-v4
QUANT_RAG_EMBEDDING_DIMENSION=1024
QUANT_RAG_EMBEDDING_BATCH_SIZE=10
```

运行诊断：

```bash
curl http://localhost:8000/api/v1/ai/runtime/status
```

初始化 Qdrant collection：

```bash
curl -X POST http://localhost:8000/api/v1/rag/collections/ensure \
  -H "Content-Type: application/json" \
  -d '{"force_recreate":false}'
```

同步新闻：

```bash
curl -X POST http://localhost:8000/api/v1/news/sync \
  -H "Content-Type: application/json" \
  -d '{"news_types":["news","notice"],"limit":100,"force_refresh":false}'
```

一键 RAG 预处理，同步执行：

```bash
curl -X POST http://localhost:8000/api/v1/rag/news/pipeline/run \
  -H "Content-Type: application/json" \
  -d '{"limit":100,"force_rechunk":false,"force_reembed":false,"ensure_collection":true,"run_embedding":true}'
```

一键 RAG 预处理，后台任务执行：

```bash
curl -X POST http://localhost:8000/api/v1/rag/news/pipeline/tasks \
  -H "Content-Type: application/json" \
  -d '{"limit":100,"force_rechunk":false,"force_reembed":false,"ensure_collection":true,"run_embedding":true}'
```

查询任务状态：

```bash
curl http://localhost:8000/api/v1/rag/news/pipeline/tasks/{execution_id}
```

搜索 RAG 新闻：

```bash
curl "http://localhost:8000/api/v1/rag/news/search?query=新能源&limit=5"
```

## 第十四步：运行定时任务

系统包含 APScheduler 定时任务，用于开盘自动扫描和收盘自动平仓。默认不会自动实盘，只会走模拟交易。相关配置包括：

```env
QUANT_ALLOW_AUTO_BUY=false
QUANT_ALLOW_AUTO_CLOSE=true
QUANT_MARKET_OPEN_BUY_TIME=09:31
QUANT_SCHEDULED_CLOSE_TIME=14:55
```

如果你启用自动买入，请先理解策略逻辑，并确认它只会影响模拟账户。

## 第十五步：Docker 部署

如果仓库包含 Dockerfile 和 docker-compose.yml，可以使用：

```bash
docker compose up -d --build
```

查看日志：

```bash
docker compose logs -f backend
```

进入容器检查配置：

```bash
docker compose exec backend python - <<'PY'
from quant_system.core.config import settings
print(settings.environment)
print(settings.database_backend)
print(settings.rag_enabled)
print(bool(settings.llm_api_key))
print(bool(settings.rag_embedding_api_key))
PY
```

## 第十六步：导入接口文档

仓库中提供两个接口文档文件。`API.postman_collection.json` 可以导入 Postman、Apifox 或兼容工具。`openapi.json` 是简化 OpenAPI 文档，可供网关、文档平台或二次生成工具使用。后端运行后，FastAPI 也会自动提供完整实时 OpenAPI：

```text
http://localhost:8000/openapi.json
http://localhost:8000/docs
```

## 第十七步：开发工作流

修改代码后建议执行：

```bash
pytest
python -m uvicorn quant_system.main:app --reload
```

新增数据库表时建议先改 ORM model，然后创建 Alembic 迁移：

```bash
alembic revision --autogenerate -m "describe your change"
alembic upgrade head
```

新增 API 时请在 `src/quant_system/api/routes.py` 中添加路由，在 `src/quant_system/api/schemas.py` 中补充请求/响应 schema，并同步更新接口文档。

## 配置项说明

常用配置项如下。`QUANT_DATABASE_BACKEND` 可选 `sqlite` 或 `mysql`。`QUANT_DATABASE_URL` 是 MySQL 连接串。`QUANT_REDIS_ENABLED` 控制 Redis 缓存。`QUANT_RAG_ENABLED` 控制 RAG。`QUANT_AI_MOCK_ENABLED` 控制是否使用 Mock AI。`QUANT_LLM_BASE_URL`、`QUANT_LLM_API_KEY`、`QUANT_LLM_MODEL` 控制大模型。`QUANT_RAG_EMBEDDING_BASE_URL`、`QUANT_RAG_EMBEDDING_API_KEY`、`QUANT_RAG_EMBEDDING_MODEL`、`QUANT_RAG_EMBEDDING_DIMENSION` 控制 Embedding。`QUANT_AI_MAX_PROMPT_CHARS`、`QUANT_AI_MAX_RAG_CONTEXT_CHARS`、`QUANT_AI_MAX_RAG_CITATIONS` 用于 AI 成本控制。`QUANT_RAG_SKIP_EMBEDDING_IF_PENDING_OVER` 用于防止一次性向量化过多 chunk。

## 安全与开源注意事项

开源前请确认没有提交 `.env`、数据库文件、缓存目录、日志文件、API Key、服务器 IP 密码、真实账户信息和个人隐私数据。建议 `.gitignore` 至少忽略 `.env`、`data/*.db`、`data/cache/`、`__pycache__/`、`.pytest_cache/`、`.venv/`、`dist/`、`node_modules/`、`*.zip`、`*.tar.gz` 和日志文件。

## 免责声明

本项目仅用于技术研究、量化系统学习和模拟交易演示，不构成投资建议。A 股市场存在风险，任何交易决策都应由使用者自行判断并承担后果。项目作者不对任何投资亏损、数据错误、模型错误、系统故障或误用承担责任。

## License

MIT License. See `LICENSE` for details.
