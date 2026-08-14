"""程序化生成 Live2D 动作文件（motion3.json）。

用法：python tools/gen_motions.py [模型目录] [保持参数]
默认模型目录：pet_shell/src/assets/live2d/chino
保持参数：长待机演出（coin_sway）中要保持为 1 的道具/手型参数，逗号分隔，
默认 Param149（智乃提币手型）；"-" 表示无（纯摇摆版）；
ariu 用 "quinzi,shoubing"（裙子+手柄，见下方 ariu 专属集）。
idle_sway 会携带这些参数的归零曲线，确保演出结束/被打断后道具复位。

生成动作：
- nod        点头（ParamAngleY 正弦两个来回，1.2s）
- shake      摇头（ParamAngleX 正弦两个来回，1.4s）
- tilt       歪头（ParamAngleZ 0->12 停顿->回正，1.6s）
- sway       身体摇摆（ParamBodyAngleZ 与 ParamAngleZ 反向联动，2.4s）
- idle_sway  待机增强版：原 idle 曲线的微妙变化 + 低频头部/身体摆动（Loop）

模型目录名为 ariu 时追加猫耳专属集（基于参数探测：Param14/15=耳竖、Param12/13=耳耷、
ParamBodyAngleZ2=扭身、Param18=瞳孔放大）：
- ear_perk   猫耳快竖两下保持（"咦？"），带头侧+瞳孔放大（1.2s）
- ear_wiggle 猫耳高频小抖 3 下（得意）（1.5s）
- ear_fold   飞机耳+低头（心虚）（2.4s）
- twist      扭身撒娇（2.5s）
- curious    歪头+耳竖+瞳孔放大（好奇）（2.0s）
且 idle_sway 追加双耳不同相位微颤曲线。

生成后自动注册到模型目录下的 *.model3.json（Motions 分组）。
"""

import json
import math
import os
import sys

FPS = 30

# 动作幅度（角度，Cubism Angle 系参数量程 ±30）
AMPLITUDES = {
    "nod_angle_y": 20.0,      # 点头
    "shake_angle_x": 20.0,    # 摇头
    "tilt_angle_z": 16.0,     # 歪头
    "sway_body_z": 7.0,       # 摇摆（身体，与头同向）
    "sway_head_z": 10.0,      # 摇摆（头部，与身同向）
}


def sine_curve(param_id, duration, amplitude, cycles, phase=0.0):
    """按 FPS 采样正弦曲线，返回 Curves 条目。"""
    n = int(duration * FPS)
    segments = []
    for i in range(n + 1):
        t = i / FPS
        v = amplitude * math.sin(2 * math.pi * cycles * t / duration + phase)
        segments += [round(t, 4), round(v, 4)]
        if i < n:
            segments.append(0)  # 线性插值
    return {"Target": "Parameter", "Id": param_id, "Segments": segments}


def _smoothstep(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def damped_shake_curve(param_id, duration, amplitude, cycles, ramp_up=0.2, ramp_down=0.35):
    """包络减幅振荡：同频率摇晃 cycles 个来回，渐入渐出，起止速度为零。"""
    n = int(duration * FPS)
    segments = []
    for i in range(n + 1):
        t = i / FPS
        env = 1.0
        if t < ramp_up:
            env = _smoothstep(t / ramp_up)
        elif t > duration - ramp_down:
            env = _smoothstep((duration - t) / ramp_down)
        # -cos 载波：从 0 起步先向负侧摆，同频率 cycles 个来回
        v = amplitude * env * (-math.cos(2 * math.pi * cycles * t / duration))
        segments += [round(t, 4), round(v, 4)]
        if i < n:
            segments.append(0)
    return {"Target": "Parameter", "Id": param_id, "Segments": segments}


def keyframe_curve(param_id, points):
    """关键帧线性插值。points: [(t, v), ...]"""
    segments = []
    for i, (t, v) in enumerate(points):
        segments += [t, v]
        if i < len(points) - 1:
            segments.append(0)
    return {"Target": "Parameter", "Id": param_id, "Segments": segments}


def _win_sin(t, t0, t1, amp, cycles, phase=0.0, ramp=0.15):
    """窗口正弦：[t0,t1] 外恒 0，窗口两端 smoothstep 渐入渐出防跳变。"""
    if t < t0 or t > t1:
        return 0.0
    tau = t - t0
    env = _smoothstep(max(0.0, min(1.0, tau / ramp, (t1 - t0 - tau) / ramp)))
    return amp * env * math.sin(2 * math.pi * cycles * tau / (t1 - t0) + phase)


def sampled_curve(param_id, duration, fn):
    """按 30fps 采样任意函数 fn(t) 生成曲线（可把多个窗口正弦叠加进同一条参数曲线）。"""
    n = int(duration * FPS)
    pts = []
    for i in range(n + 1):
        t = i / FPS
        pts.append((round(t, 4), round(fn(t), 4)))
    return keyframe_curve(param_id, pts)


def envelope_sine_curve(param_id, duration, amplitude, env_base=0.65, env_amp=0.35, cycles=1):
    """幅度包络正弦：载波为整周期慢摆，幅度按 env_base±env_amp 周期起伏。

    幅度包络 = env_base + env_amp*sin(2πt/T)，与载波同相位：
    中幅起步 → 扩大 → 回中幅 → 收窄 → 回中幅，循环点处平滑连续。
    cycles: 每个循环内载波次数。
    """
    n = int(duration * FPS)
    segments = []
    for i in range(n + 1):
        t = i / FPS
        env = env_base + env_amp * math.sin(2 * math.pi * t / duration)
        v = amplitude * env * math.sin(2 * math.pi * cycles * t / duration)
        segments += [round(t, 4), round(v, 4)]
        if i < n:
            segments.append(0)
    return {"Target": "Parameter", "Id": param_id, "Segments": segments}


def apply_fade_out(curve, duration, fade_len=4.0):
    """给曲线末尾叠加淡出包络：fade_len 秒内按 smoothstep 收归 0，消除动作结束的割裂感。"""
    fade_start = duration - fade_len
    segs = curve["Segments"]
    out = []
    for i in range(0, len(segs), 3):
        t, v = segs[i], segs[i + 1]
        if t > fade_start:
            k = max(0.0, (duration - t) / fade_len)
            v *= _smoothstep(k)
        out += [round(t, 4), round(v, 4)]
        if i + 2 < len(segs):
            out.append(segs[i + 2])
    return {"Target": curve["Target"], "Id": curve["Id"], "Segments": out}


def make_motion(duration, curves, loop=False):
    point_count = sum((len(c["Segments"]) + 1) // 3 + 1 for c in curves)
    return {
        "Version": 3,
        "Meta": {
            "Duration": duration,
            "Fps": float(FPS),
            "Loop": loop,
            "AreBeziersRestricted": False,
            "CurveCount": len(curves),
            "TotalSegmentCount": sum(len(c["Segments"]) // 3 for c in curves),
            "TotalPointCount": point_count,
            "UserDataCount": 0,
            "TotalUserDataSize": 0,
        },
        "Curves": curves,
    }


def main():
    model_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "src", "assets", "live2d", "chino",
    )
    # 保持参数列表（智乃=Param149 提币手型；ariu=quinzi,shoubing 裙子+手柄；"-"=无）
    hold_arg = sys.argv[2] if len(sys.argv) > 2 else "Param149"
    hold_params = [] if hold_arg == "-" else [p for p in hold_arg.split(",") if p]
    motions_dir = os.path.join(model_dir, "motions")
    os.makedirs(motions_dir, exist_ok=True)

    generated = {
        "nod": make_motion(1.4, [sine_curve("ParamAngleY", 1.4, AMPLITUDES["nod_angle_y"], 2)]),
        "shake": make_motion(2.0, [damped_shake_curve("ParamAngleX", 2.0, AMPLITUDES["shake_angle_x"], 3)]),
        "tilt": make_motion(1.6, [keyframe_curve(
            "ParamAngleZ", [(0, 0), (0.4, AMPLITUDES["tilt_angle_z"]),
                            (1.0, AMPLITUDES["tilt_angle_z"]), (1.6, 0)])]),
        # 左右侧倾摇摆：头身同向，参考视线跟随的倾角幅度，3 个来回
        "sway": make_motion(3.0, [
            damped_shake_curve("ParamAngleZ", 3.0, AMPLITUDES["sway_head_z"], 3),
            damped_shake_curve("ParamBodyAngleZ", 3.0, AMPLITUDES["sway_body_z"], 3),
        ]),
    }

    # idle_sway：原 idle 曲线 + 低频摆动（待机增强）
    # 附带保持参数归零曲线：确保 coin_sway 结束/被打断后道具/手型复位
    idle_path = os.path.join(motions_dir, "idle.motion3.json")
    idle_curves = []
    if os.path.exists(idle_path):
        idle_curves = json.load(open(idle_path, encoding="utf-8")).get("Curves", [])
    hold_reset = [keyframe_curve(p, [(0, 0), (6.0, 0)]) for p in hold_params]
    generated["idle_sway"] = make_motion(6.0, idle_curves + hold_reset + [
        sine_curve("ParamAngleZ", 6.0, 1.8, 1),
        sine_curve("ParamBodyAngleZ", 6.0, 1.2, 1, phase=math.pi / 2),
        sine_curve("ParamAngleX", 6.0, 1.0, 2),
    ], loop=True)

    # coin_sway：60s 单次长待机演出（不循环），末尾 8s 曲线内淡出，平滑接回待机
    # - 保持参数（智乃 Param149 / ariu quinzi+shoubing）：1s 淡入保持，52s 起 8s 淡出放下
    # - 头部/上身：整周期慢摆，幅度包络 中→大→中→小→中，末 8s 幅度渐收至 0
    # - 播完自然触发 motionFinish 回 idle_sway，无割裂
    COIN_DURATION = 60.0
    COIN_FADE = 8.0
    hand_hold = [keyframe_curve(p, [(0, 0), (1.0, 1.0), (COIN_DURATION - COIN_FADE, 1.0), (COIN_DURATION, 0)]) for p in hold_params]
    generated["coin_sway"] = make_motion(COIN_DURATION, idle_curves + hand_hold + [
        apply_fade_out(envelope_sine_curve("ParamAngleZ", COIN_DURATION, 12.0, cycles=12), COIN_DURATION, COIN_FADE),
        apply_fade_out(envelope_sine_curve("ParamBodyAngleZ", COIN_DURATION, 11.0, cycles=12), COIN_DURATION, COIN_FADE),
        apply_fade_out(envelope_sine_curve("ParamBodyAngleX", COIN_DURATION, 5.5, cycles=24), COIN_DURATION, COIN_FADE),  # 身体轻微摇感
        apply_fade_out(sine_curve("ParamAngleX", COIN_DURATION, 1.0, 25), COIN_DURATION, COIN_FADE),  # 微幅点头保持灵动感
    ], loop=False)

    # ariu 专属动作集：猫耳（Param14/15=竖起，Param12/13=耷拉）+ 扭身（ParamBodyAngleZ2）
    # + 瞳孔放大（Param18，非眨眼通道可进动作曲线）；手部参数仅转手不做抬臂（手臂是烘焙美术件）
    if os.path.basename(os.path.normpath(model_dir)) == "ariu":
        # 猫耳竖起（注意到什么的"咦？"瞬间）：双耳快竖两下→保持→放松，头微侧
        generated["ear_perk"] = make_motion(1.2, [
            keyframe_curve("Param14", [(0, 0), (0.12, 12), (0.3, 4), (0.45, 12), (0.8, 12), (1.2, 0)]),
            keyframe_curve("Param15", [(0, 0), (0.12, 12), (0.3, 4), (0.45, 12), (0.8, 12), (1.2, 0)]),
            keyframe_curve("ParamAngleZ", [(0, 0), (0.3, 5), (1.2, 0)]),
            keyframe_curve("Param18", [(0, 0), (0.2, 0.5), (0.9, 0.5), (1.2, 0)]),
        ])
        # 猫耳抖动：双耳高频小幅抖 3 下（得意/开心）
        generated["ear_wiggle"] = make_motion(1.5, [
            damped_shake_curve("Param14", 1.5, 5.0, 3, ramp_up=0.15, ramp_down=0.4),
            damped_shake_curve("Param15", 1.5, 5.0, 3, ramp_up=0.15, ramp_down=0.4),
            damped_shake_curve("ParamAngleZ", 1.5, 3.0, 3, ramp_up=0.15, ramp_down=0.4),
        ])
        # 飞机耳+低头（心虚/不好意思）：双耳耷拉保持，头微低
        generated["ear_fold"] = make_motion(2.4, [
            keyframe_curve("Param12", [(0, 0), (0.4, -18), (1.6, -18), (2.4, 0)]),
            keyframe_curve("Param13", [(0, 0), (0.4, -18), (1.6, -18), (2.4, 0)]),
            keyframe_curve("ParamAngleY", [(0, 0), (0.5, 10), (1.6, 10), (2.4, 0)]),
        ])
        # 扭捏：身体扭转左右晃（撒娇感），头反向小联动
        generated["twist"] = make_motion(2.5, [
            damped_shake_curve("ParamBodyAngleZ2", 2.5, 8.0, 3),
            damped_shake_curve("ParamAngleZ", 2.5, 4.0, 3),
            damped_shake_curve("ParamAngleX", 2.5, 3.0, 3),
        ])
        # 好奇：歪头保持+猫耳竖起+瞳孔放大
        generated["curious"] = make_motion(2.0, [
            keyframe_curve("ParamAngleZ", [(0, 0), (0.5, 14), (1.4, 14), (2.0, 0)]),
            keyframe_curve("Param14", [(0, 0), (0.3, 10), (1.4, 10), (2.0, 0)]),
            keyframe_curve("Param15", [(0, 0), (0.3, 10), (1.4, 10), (2.0, 0)]),
            keyframe_curve("Param18", [(0, 0), (0.4, 0.6), (1.4, 0.6), (2.0, 0)]),
        ])
        # 待机增强：在原 3 曲线基础上加猫耳微动（双耳不同相位，似呼吸般轻颤）
        generated["idle_sway"] = make_motion(6.0, idle_curves + hold_reset + [
            sine_curve("ParamAngleZ", 6.0, 1.8, 1),
            sine_curve("ParamBodyAngleZ", 6.0, 1.2, 1, phase=math.pi / 2),
            sine_curve("ParamAngleX", 6.0, 1.0, 2),
            sine_curve("Param14", 6.0, 1.5, 1, phase=math.pi / 4),
            sine_curve("Param15", 6.0, 1.5, 1, phase=math.pi / 3),
        ], loop=True)
        # 长待机演出加猫耳慢摇（与待机同款，随末 8s 一起淡出）
        generated["coin_sway"] = make_motion(COIN_DURATION, idle_curves + hand_hold + [
            apply_fade_out(envelope_sine_curve("ParamAngleZ", COIN_DURATION, 12.0, cycles=12), COIN_DURATION, COIN_FADE),
            apply_fade_out(envelope_sine_curve("ParamBodyAngleZ", COIN_DURATION, 11.0, cycles=12), COIN_DURATION, COIN_FADE),
            apply_fade_out(envelope_sine_curve("ParamBodyAngleX", COIN_DURATION, 5.5, cycles=24), COIN_DURATION, COIN_FADE),
            apply_fade_out(sine_curve("ParamAngleX", COIN_DURATION, 1.0, 25), COIN_DURATION, COIN_FADE),
            apply_fade_out(sine_curve("Param14", COIN_DURATION, 2.0, 12, phase=math.pi / 4), COIN_DURATION, COIN_FADE),
            apply_fade_out(sine_curve("Param15", COIN_DURATION, 2.0, 12, phase=math.pi / 3), COIN_DURATION, COIN_FADE),
        ], loop=False)

    for name, motion in generated.items():
        out = os.path.join(motions_dir, f"{name}.motion3.json")
        json.dump(motion, open(out, "w", encoding="utf-8"), ensure_ascii=False)
        print("written:", out)
    # 注册到 model3.json
    mj_path = None
    for f in os.listdir(model_dir):
        if f.endswith(".model3.json"):
            mj_path = os.path.join(model_dir, f)
            break
    if not mj_path:
        print("!! 未找到 model3.json，跳过注册")
        return
    d = json.load(open(mj_path, encoding="utf-8"))
    motions = d["FileReferences"].setdefault("Motions", {})
    for name in generated:
        if name not in motions:
            motions[name] = [{"File": f"motions/{name}.motion3.json",
                              "FadeInTime": 0.4, "FadeOutTime": 0.4}]
    json.dump(d, open(mj_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("registered motion groups:", list(motions))


if __name__ == "__main__":
    main()
