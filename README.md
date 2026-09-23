# BagRoute

投递装袋：按路线订户顺序装袋，重量与体积双约束，超限拒收。

## 启动

```bash
docker compose up --build
```

| 服务 | 地址 |
| --- | --- |
| 前端 | http://localhost:4300 |
| API | http://localhost:9300 |
| API 文档 | http://localhost:9300/docs |
| Postgres | localhost:5444 |

健康检查：`GET http://localhost:9300/api/health`

## 页面

- `/routes` — 路线
- `/stops` — 订户点
- `/pack` — 装袋
- `/bags` — 袋明细
- `/rejects` — 拒收
- `/weights` — 袋重

## 使用说明

1. 查看路线与订户点顺序。
2. 在装袋页选择路线执行双约束装袋。
3. 袋明细与袋重查看结果，拒收页查看超限订户。

### 调整路线限额

重量上限与体积上限只允许在路线**没有袋明细且没有拒收**时修改：

1. 路线仍有袋或拒收占用时保存限额会被拒绝（HTTP 409），页面提示先清空已装袋，库里的限额保持不变。
2. 在路线页（或装袋页）执行「清空装袋」：删除该路线的袋、袋内站点明细与拒收，袋重页同步不再出现这些袋。
3. 清空后保存新的重量/体积上限。
4. 回到装袋页重新装袋，开袋与拒收均按新限额判定。

## 开发与测试

```bash
docker compose exec api pytest -q
```
