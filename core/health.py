# -*- coding: utf-8 -*-
"""健康评分引擎。

把 smart_engine 采集到的原始 S.M.A.R.T. 数据换算成：
    - 0~100 健康分
    - 等级（优秀 / 良好 / 注意 / 危险）
    - 面向普通用户的结论条目（findings）
    - 卡片上展示的关键指标（metrics）
    - 完整属性表（attributes）

设计原则：
    1. 只对「确定意味着可靠性下降」的指标扣分，避免误报吓到用户；
    2. 每个扣分项都给出人类可读的原因，而不是只吐一个数字；
    3. ATA 与 NVMe 各写一套规则——两者的失效模型完全不同。
"""
from __future__ import annotations

SEV_OK, SEV_INFO, SEV_WARN, SEV_CRIT = "ok", "info", "warn", "crit"

LEVELS = (
    (90, "优秀", "#3ddc84"),
    (75, "良好", "#4aa8ff"),
    (50, "注意", "#ffb020"),
    (0, "危险", "#ff4d4f"),
)


def level_of(score: int):
    for threshold, name, color in LEVELS:
        if score >= threshold:
            return name, color
    return "危险", "#ff4d4f"


def _f(sev, text):
    return {"sev": sev, "text": text}


# --------------------------------------------------------------------------
# ATA / SATA
# --------------------------------------------------------------------------
# (属性ID, 显示名, 每单位扣分, 最大扣分, 严重级别, 说明模板)
ATA_RULES = (
    (5, "重映射扇区计数", 2.0, 40, SEV_CRIT,
     "已有 {n} 个扇区损坏并被备用扇区替换"),
    (196, "重映射事件计数", 2.0, 30, SEV_WARN,
     "发生过 {n} 次扇区重映射事件"),
    (197, "当前待映射扇区", 3.0, 45, SEV_CRIT,
     "有 {n} 个扇区正在等待重映射（读取不稳定）"),
    (198, "无法校正的扇区", 3.0, 45, SEV_CRIT,
     "有 {n} 个扇区无法校正（离线扫描发现坏道）"),
    (187, "报告的不可纠正错误", 2.5, 35, SEV_CRIT,
     "报告了 {n} 次无法纠正的读取错误"),
    (199, "UDMA CRC 错误", 0.5, 15, SEV_WARN,
     "数据线传输出现 {n} 次 CRC 校验错误（多为 SATA 线/接口问题）"),
    (184, "端到端错误", 5.0, 30, SEV_CRIT,
     "发生 {n} 次端到端数据校验失败"),
    (188, "命令超时", 0.5, 10, SEV_WARN,
     "有 {n} 次命令超时（可能是线材或供电问题）"),
)

# SSD 剩余寿命属性：值是「剩余百分比」，越高越好
ATA_LIFE_ATTRS = {231: "SSD 剩余寿命", 233: "介质磨损指示"}

ATA_TEMP_ATTRS = (194, 190)


def score_ata(index: int, device: dict, parsed: dict, warnings) -> dict:
    attrs = {a["id"]: a for a in parsed.get("attributes", [])}
    score = 100.0
    findings = []

    if not parsed.get("checksum_ok", False):
        score -= 15
        findings.append(_f(SEV_WARN, "SMART 数据校验和错误，读数可能不可信"))

    # --- 坏道 / 错误类 ---
    for aid, name, per, cap, sev, tpl in ATA_RULES:
        a = attrs.get(aid)
        if not a:
            continue
        n = a["raw"]
        if n <= 0:
            continue
        score -= min(n * per, cap)
        findings.append(_f(sev, tpl.format(n=n)))

    # --- SSD 寿命 ---
    for aid, name in ATA_LIFE_ATTRS.items():
        a = attrs.get(aid)
        if not a:
            continue
        left = a["value"]
        if left >= 100:
            continue
        penalty = min((100 - left) * 0.8, 60)
        score -= penalty
        sev = SEV_CRIT if left < 20 else (SEV_WARN if left < 60 else SEV_INFO)
        findings.append(_f(sev, f"{name}剩余 {left}%"))

    # --- 温度 ---
    temp = None
    for aid in ATA_TEMP_ATTRS:
        a = attrs.get(aid)
        if not a:
            continue
        b0 = a["raw_first_byte"]
        if b0 in (0, 255):
            continue
        temp = b0
        break
    if temp is not None:
        if temp >= 60:
            score -= 12
            findings.append(_f(SEV_CRIT, f"温度 {temp}°C，明显偏高，请检查散热"))
        elif temp >= 50:
            score -= 5
            findings.append(_f(SEV_WARN, f"温度 {temp}°C，偏高"))
        elif temp <= 10:
            score -= 3
            findings.append(_f(SEV_INFO, f"温度 {temp}°C，偏低"))

    score = max(0, min(100, round(score)))

    hours = attrs.get(9, {}).get("raw")
    cycles = attrs.get(12, {}).get("raw")
    metrics = [
        ("温度", temp if temp is not None else "—", "°C"),
        ("通电时间", hours if hours is not None else "—", "小时"),
        ("通电次数", cycles if cycles is not None else "—", "次"),
        ("重映射扇区", attrs.get(5, {}).get("raw", 0), "个"),
        ("待映射扇区", attrs.get(197, {}).get("raw", 0), "个"),
        ("CRC 错误", attrs.get(199, {}).get("raw", 0), "次"),
    ]
    if not findings:
        findings.append(_f(SEV_OK, "所有关键 S.M.A.R.T. 指标均正常"))

    return {
        "score": score,
        "findings": findings,
        "metrics": metrics,
        "attributes": parsed.get("attributes", []),
        "kind": "ata",
        "extra_warnings": list(warnings or []),
    }


# --------------------------------------------------------------------------
# NVMe
# --------------------------------------------------------------------------
NVME_CRIT_BITS = (
    "可用备用空间低于阈值", "温度超过阈值", "NVM 子系统可靠性降级", "已进入只读模式",
    "易失性内存备份失败", "易失性内存未就绪", "控制器内存缓冲区错误", "保留位",
)


def score_nvme(index: int, device: dict, health: dict) -> dict:
    score = 100.0
    findings = []

    cw = int(health.get("critical_warning", 0) or 0)
    for bit, label in enumerate(NVME_CRIT_BITS):
        if cw >> bit & 1:
            if bit == 3:
                score -= 60
                findings.append(_f(SEV_CRIT, "固态硬盘已进入只读模式，随时可能完全失效"))
            elif bit == 0:
                score -= 30
                findings.append(_f(SEV_CRIT, "可用备用空间已低于厂商阈值"))
            elif bit == 1:
                score -= 15
                findings.append(_f(SEV_CRIT, "温度已超过厂商阈值"))
            else:
                score -= 12
                findings.append(_f(SEV_WARN, f"固件警告：{label}"))

    wear = int(health.get("percentage_used_pct", 0) or 0)
    if wear > 0:
        score -= min(wear * 0.6, 60)
        sev = SEV_CRIT if wear >= 80 else (SEV_WARN if wear >= 50 else SEV_INFO)
        findings.append(_f(sev, f"寿命已消耗 {wear}%（剩余约 {100 - wear}%）"))

    spare = int(health.get("available_spare_pct", 100) or 0)
    thr = int(health.get("available_spare_threshold_pct", 10) or 0)
    if spare < thr:
        score -= 20
        findings.append(_f(SEV_CRIT, f"可用备用块 {spare}% 已低于阈值 {thr}%"))
    elif spare < 20:
        score -= 6
        findings.append(_f(SEV_WARN, f"可用备用块仅剩 {spare}%"))

    media_err = int(health.get("media_errors", 0) or 0)
    if media_err > 0:
        score -= min(media_err * 2.0, 40)
        findings.append(_f(SEV_CRIT, f"检测到 {media_err} 次介质与数据完整性错误"))

    unsafe = int(health.get("unsafe_shutdowns", 0) or 0)
    if unsafe > 0:
        score -= min(unsafe * 0.2, 8)
        findings.append(_f(SEV_INFO, f"存在 {unsafe} 次不安全关机（通常无害，建议正常关机）"))

    temp = health.get("temperature_c")
    if isinstance(temp, (int, float)):
        if temp >= 70:
            score -= 12
            findings.append(_f(SEV_CRIT, f"温度 {temp}°C，明显偏高"))
        elif temp >= 60:
            score -= 5
            findings.append(_f(SEV_WARN, f"温度 {temp}°C，偏高"))

    score = max(0, min(100, round(score)))

    metrics = [
        ("温度", temp if temp is not None else "—", "°C"),
        ("通电时间", health.get("power_on_hours", "—"), "小时"),
        ("通电次数", health.get("power_cycles", "—"), "次"),
        ("寿命消耗", wear, "%"),
        ("可用备用块", spare, "%"),
        ("介质错误", media_err, "次"),
    ]

    attributes = [
        {"id": "—", "name": "写入总量", "value": health.get("data_units_written_gb"),
         "worst": "—", "raw": f'{health.get("data_units_written_gb", 0)} GB'},
        {"id": "—", "name": "读取总量", "value": health.get("data_units_read_gb"),
         "worst": "—", "raw": f'{health.get("data_units_read_gb", 0)} GB'},
        {"id": "—", "name": "不安全关机", "value": unsafe, "worst": "—", "raw": unsafe},
        {"id": "—", "name": "错误日志条目", "value": health.get("error_log_entries"),
         "worst": "—", "raw": health.get("error_log_entries")},
        {"id": "—", "name": "控制器忙碌", "value": health.get("controller_busy_minutes"),
         "worst": "—", "raw": f'{health.get("controller_busy_minutes", 0)} 分钟'},
        {"id": "—", "name": "警告温度时长", "value": health.get("warning_temp_minutes"),
         "worst": "—", "raw": f'{health.get("warning_temp_minutes", 0)} 分钟'},
        {"id": "—", "name": "临界温度时长", "value": health.get("critical_temp_minutes"),
         "worst": "—", "raw": f'{health.get("critical_temp_minutes", 0)} 分钟'},
    ]

    if not findings:
        findings.append(_f(SEV_OK, "所有关键 S.M.A.R.T. 指标均正常"))

    return {
        "score": score,
        "findings": findings,
        "metrics": metrics,
        "attributes": attributes,
        "kind": "nvme",
        "extra_warnings": [],
    }


# --------------------------------------------------------------------------
# 失败 / 未知
# --------------------------------------------------------------------------
def score_unavailable(index: int, device: dict, reason: str) -> dict:
    return {
        "score": None,
        "findings": [_f(SEV_WARN, reason)],
        "metrics": [],
        "attributes": [],
        "kind": "unknown",
        "extra_warnings": [],
    }


# --------------------------------------------------------------------------
# 统一入口
# --------------------------------------------------------------------------
def evaluate(result: dict) -> dict:
    """把 smart_engine.probe_drive 的返回值换算成健康结论。"""
    index = result.get("index")
    device = result.get("device") or {}
    channels = result.get("channels") or {}
    readable = result.get("readable") or []

    info = {
        "index": index,
        "model": ("%s %s" % (device.get("vendor", ""), device.get("product", ""))).strip()
                 or f"物理磁盘 {index}",
        "serial": device.get("serial", ""),
        "bus": device.get("bus_type", "?"),
        "readable": readable,
        "predict_failure": (result.get("predict_failure") or {}).get("predict_failure"),
    }

    if not readable:
        reason = result.get("open_error") or "无法读取该磁盘的 S.M.A.R.T. 数据"
        verdict = score_unavailable(index, device, reason)
    elif "nvme" in channels and channels["nvme"].get("ok"):
        verdict = score_nvme(index, device, channels["nvme"].get("parsed", {}))
    else:
        name = next((k for k in ("ata_pass_through", "smart_rcv_drive_data", "scsi_ata16")
                     if channels.get(k, {}).get("ok")), None)
        if name:
            ch = channels[name]
            verdict = score_ata(index, device, ch.get("parsed", {}), ch.get("warnings"))
        else:
            verdict = score_unavailable(index, device, "未能通过任何通道读取到数据")

    # 固件级故障预警：一票否决
    if info["predict_failure"] == 1:
        verdict["score"] = min(verdict["score"] if verdict["score"] is not None else 30, 15)
        verdict["findings"].insert(0, _f(SEV_CRIT, "硬盘固件已发出故障预警（S.M.A.R.T. FAILED）"))

    info.update(verdict)
    if info["score"] is None:
        info["level"], info["color"] = "无法读取", "#7a8194"
    else:
        info["level"], info["color"] = level_of(info["score"])
    return info


def evaluate_all(results) -> list:
    return [evaluate(r) for r in results]
