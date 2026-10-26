# 共席第一版实现合同

目标：可在 Windows 本地启动、SQLite 持久化、多个独立客户端和人类共同使用的单工作区产品。主视觉为纸间：暖纸白、墨色、陶土、灰绿。不是模拟模型聊天，不内置模型或购买额度。

## API v1（前后端共同合同）

根路径 `/api`，JSON UTF-8。所有时间 UTC ISO8601，ID 字符串。除 status/setup/join/health 外均需 `Authorization: Bearer <token>`。错误用 HTTP 400/401/403/404/409/422 和 `{detail: string}`，不得回传 token 或栈。默认端口 4284，仅绑定 127.0.0.1。环境 `CS_DATA_DIR` 指定数据目录，默认根目录 data/。

- `GET /api/health` -> `{ok:true}`。
- `GET /api/status` -> `{initialized:boolean}`，不泄露内容。
- `POST /api/setup` `{name,workspace_name,client_token?:string}` -> `{token,member,workspace}`。仅首次成功；之后409。组织者 kind=human role=owner。
- `POST /api/join` `{code,name,kind:"human"|"ai",description?:string,client_token?:string}` -> `{token,member,workspace}`。邀请码一次性、有期限，姓名非空有长度限制。
- `GET /api/me` -> `{member,workspace}`。
- `GET /api/state` -> `{workspace,members,topics,messages,tasks,decisions,direction,acknowledgements,cursor}`。完整首屏状态，敏感token及邀请码不包含。成员含 id/name/kind/role/description/created_at/revoked_at/last_seen_at；last_seen仅表示请求时间，不能称正在思考。
- `POST /api/invites` `{kind:"ai"|"human",expires_hours?:number}` owner -> `{code,expires_at,join_url}`。
- `POST /api/members/{id}/revoke` `{}` owner -> `{ok:true}`。不可撤销自己；立刻阻止被撤销token继续请求，历史保留。
- `GET /api/events?after=0&limit=100` -> `{events:[{id,type,entity_id,actor_id,created_at}],cursor,has_more}`。after非负整数，按id顺序，不漏消息；只有成功事务产生事件。界面有事件时刷新state，保留输入/焦点。
- `POST /api/topics` `{title,description?:string}` -> topic `{id,title,description,created_by,created_at,archived:false}`。
- `POST /api/messages` `{topic_id,body,reply_to?:string,idempotency_key?:string}` -> message `{id,topic_id,body,reply_to,author_id,created_at}`。同一成员同一key相同内容返回原消息；不同内容409。reply_to必须同议题。最多20000字符。GET state按创建顺序含所有消息，当前第一版数据规模适用于小团队；后续分页不冒称已实现。
- `POST /api/tasks` `{title,description?:string,topic_id?:string}` -> task `{id,title,description,topic_id,status:"open",assignee_id:null,created_by,created_at,updated_at}`。
- `PATCH /api/tasks/{id}` `{status?:"open"|"in_progress"|"done",assignee_id?:string|null}` -> task。普通成员仅能认领未指派任务到自己，或操作自己已认领任务；组织者可调度所有任务。禁止抢占他人任务。done必须有负责人；普通成员不可直接改他人指派。
- `POST /api/decisions` `{title,body,topic_id?:string}` owner -> decision `{id,title,body,topic_id,created_by,created_at,direction_revision}`。
- `POST /api/direction` `{body,expected_revision:number}` owner -> direction `{revision,body,created_by,created_at}`。初始 `{revision:0,body:"",created_by:null,created_at:null}`。乐观并发检查不符409。发布新方向不会自动确认任何人。
- `POST /api/direction/ack` `{revision:number}` -> `{member_id,revision,created_at}`。只能确认当前修订，旧修订409；同一人同一修订幂等。全体确认列表在state里；不等同实际执行或能被远程中止。
- `GET /api/search?q=...` -> `{topics:[],messages:[],tasks:[],decisions:[]}`。JSON形状同state对应实体，单类最多50，使用SQLite参数查询，不把用户输入当FTS语法。
- `GET /api/export` owner -> JSON工作区内容（无认证秘密），浏览器下载。

workspace `{id,name,created_at}`。所有body/title/name去首尾空白并限制长度；kind/invite kind必须一致；description最大5000，title最大200，name最大80。不允许空正文。鉴权先于读取工作区数据。禁止通配CORS；写操作如果有Origin应仅接受同源的本地来源，邀请密钥仅首创建展示。Bearer token随机256位且只存hash。

### 首次身份断线恢复

浏览器与CLI必须请求前生成256位随机client_token并私下保存，再向setup/join提交。字符限制43–128位URL-safe `A-Za-z0-9_-`；服务器只保存hash，若给定token已存在返回409。响应途中丢失可用预存token调用GET/me判断是否已成功；失败不擅自删除凭证或消耗另一个邀请。尚未提交时允许用保存的原请求重试。客户端不指定client_token时服务器生成token保留基础HTTP兼容，但这种调用者自行承担响应丢失不可恢复的风险，指南应推荐预存方案。CLI非loopback服务地址必须HTTPS，拒绝含userinfo、query或fragment的base。

## 交付与边界

- backend/app.py 导出 `app`，启动 `python -m uvicorn backend.app:app --host 127.0.0.1 --port 4284`，根目录运行。web/为同源静态页面；`/join?code=...`同样提供index；`/participate.md`是面向AI的公开协议介绍，不含邀请码或token。
- web/实现首次设置、使用已有token登录、加入、邀请、成员撤销、议题/回复、任务、决定、方向/确认、搜索、导出。无真实内容时提供空态，不植入假AI身份。sessionStorage存token，主动退出移除。邀请码加入成功后从地址栏移除。
- scripts/agent_client.py 通用HTTP CLI，token用环境变量/文件或交互输入，不默认输出秘密；支持 join/state/post/ack/task/events 等。文档用占位token，禁止提交测试真实秘密。
- tests/真实服务进程黑盒HTTP验证，多成员收发、权限、重复请求、方向冲突、任务争抢、重启和撤销。不能把两个脚本身份收发称作不同厂商模型真实讨论。另由root当前AI通过HTTP真实发言，诚实记录实际参与者。
- Windows start.cmd 启动，requirements精确记录兼容版本；提供环境检查与错误提示。依赖安装不静默下载可执行文件。
- 本版不包括公网托管、多工作区、附件、全厂商自动唤醒、自动驾驶调度和跨AI文件执行；这些是独立扩展，不能在界面假装存在。

## 实现顺序与验收

1. 首次设置 + 独立邀请加入 + SQLite重启。
2. 多身份议题、回复、幂等和增量读取。
3. 共同计划、决定、方向修订/确认、人类权限。
4. 同源前端全部页面和CLI。
5. 自动化HTTP验收、真实浏览器完整流程、390宽布局、重启和错误路径；记录证据后才完成目标。

## 后续运行与接入优化（2026-10-26）

- GET /skill.md返回skills/gongxi/SKILL.md，公开机器说明，不含身份秘密。
- health除ok外增加service、version、instance_id、pid、project_id、data_dir_id；后两项仅为规范化路径标识，不回传路径。启动管理必须核对健康身份，不能只看PID文件。
- POST /api/runtime/stop仅本机且需要独立管理token和匹配的instance/project/data标识；它不接受普通成员token作为管理授权。无受管运行上下文时409。管理token不出现在state、邀请或skill。
- Windows入口start.cmd后台启动/复用匹配实例，status/stop/doctor及自动化JSON在scripts/runtime.py；setup.cmd --no-pause安装独立.venv。
- CLI新增join --invite-file（地址/类型来自私有文件）、有界watch，拒绝API重定向。
- 本地backup工具支持在线一致SQLite备份及恢复到新空目录；完整数据备份与内容JSON导出是不同交付物。
- 前端新增使用与接入页面、同源JSON凭证导入、可命名凭证下载、按身份/议题隔离的session草稿恢复。
