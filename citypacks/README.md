# CityPack

每座城市一份 YAML：入口在哪、怎么翻页、抽哪些字段、覆盖哪些事项。

规则：
- `status: verified` 才能进生产；`unverified` 只跑探测；`blocked` 写清原因，不删
- 一个 source 只放一个栏目页；同一部门的「政策文件 / 解读 / 公告」分开记
- 入口 URL 必须人工打开确认过一次，不允许纯脚本猜
