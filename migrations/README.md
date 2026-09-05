# 数据库迁移(migrations/)

## 这是什么
采购系统代码用 git 同步,但**数据库不跟 git 走**(data/purchase.db 每台机器各自独立)。
所以改了表结构(加表/加列/改列),光推代码没用——另一台的库里没有新结构,跑起来会报错。

本目录解决这个问题:**谁改了表结构,就把 ALTER 语句写成 .sql 文件放这里,随代码一起提交。**
另一台 `git pull` 后跑一次 `python migrate_db.py`,自动把没执行过的迁移全部应用。

## 双机铁律(两台都要遵守)
1. 改表结构 = 必须写迁移文件,不许只在自己库里手工 ALTER(另一台不知道)
2. 迁移文件命名: `YYYYMMDD_一句话说明.sql`,例如 `20260905_维修模块加字段.sql`
3. 写完迁移文件,和代码一起 commit + push
4. 另一台 pull 后,运行 `python migrate_db.py`(Windows) / `python3 migrate_db.py`(Mac)
5. migrate_db.py 会用 migrations_log 表记住已执行的,重复跑不会二次执行,放心

## 迁移文件写法
- 直接写 SQL,可多条语句,一条文件里顺序执行
- 只写结构变更(CREATE TABLE / ALTER TABLE ADD COLUMN / 索引),不写业务数据
- ALTER TABLE 加列用标准语法:`ALTER TABLE 表名 ADD COLUMN 列名 类型 [默认值]`

## 验证
跑完 migrate_db.py 后,用 check_code.py 或直接登录系统验证新功能正常。
