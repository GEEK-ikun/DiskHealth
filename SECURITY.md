# 安全说明

## 这个程序碰什么、不碰什么

| 行为 | 说明 |
| --- | --- |
| 读取物理磁盘 | 通过 `CreateFileW` 打开 `\\.\PhysicalDriveN`，只发送读取类指令 |
| 发送的 ATA 命令 | 仅 `0xB0`（SMART）+ 特性 `0xD0`（SMART READ DATA） |
| 发送的 SCSI 命令 | 仅 ATA PASS-THROUGH (16)，`T_DIR=1`，数据方向为从设备读入 |
| 写盘 | **没有**任何写盘指令 |
| 注册表 | 不读不写 |
| 系统设置 / 服务 / 计划任务 | 一个都不碰 |
| 网络 | 代码库不含 socket / http / urllib / requests |
| 写入的文件 | 只有 `launch.log`（源码运行时在项目目录，打包后在 `%LOCALAPPDATA%\DiskHealth`） |

## 权限

程序需要管理员权限，原因是 Windows 只允许管理员打开物理磁盘读取 S.M.A.R.T.。
这是操作系统的安全边界，不是本程序的额外要求。

打开磁盘句柄时，代码先请求「读 + 写」权限，失败后回退到只读。这不是为了写盘：
部分主控/驱动会拒绝在只读句柄上执行 ATA PASS-THROUGH。程序内部没有任何写盘路径。

## 报告问题

如果你在代码里发现与本说明不符的行为——尤其是任何写盘、联网、越权访问——
请通过 GitHub 的 Security Advisory（仓库页 → Security → Report a vulnerability）私下报告，
不要在 issue 里公开可被利用的细节。

请附上：受影响的文件与行号、你的 Windows 版本、以及可以复现的最小步骤。

## 支持范围

只对 `main` 分支的最新提交提供修复。程序没有自动更新机制，也不会通知用户升级。
