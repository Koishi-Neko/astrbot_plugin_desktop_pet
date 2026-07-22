"""程序化生成 Live2D 动作文件（motion3.json）。

用法：python tools/gen_motions.py [模型目录]
默认模型目录：pet_shell/src/assets/live2d/chino

生成动作：
- nod        点头（ParamAngleY 正弦两个来回，1.2s）
- shake      摇头（ParamAngleX 正弦两个来回，1.4s）
- tilt       歪头（ParamAngleZ 0->12 停顿->回正，1.6s）
- sway       身体摇摆（ParamBodyAngleZ 与 ParamAngleZ 反向联动，2.4s）
- idle_sway  待机增强版：原 idle 曲线的微妙变化 + 低频头部/身体摆动（Loop）

生成后自动注册到模型目录下的 chino.model3.json（Motions 分组）。
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
    idle_path = os.path.join(motions_dir, "idle.motion3.json")
    idle_curves = []
    if os.path.exists(idle_path):
        idle_curves = json.load(open(idle_path, encoding="utf-8")).get("Curves", [])
    generated["idle_sway"] = make_motion(6.0, idle_curves + [
        sine_curve("ParamAngleZ", 6.0, 1.8, 1),
        sine_curve("ParamBodyAngleZ", 6.0, 1.2, 1, phase=math.pi / 2),
        sine_curve("ParamAngleX", 6.0, 1.0, 2),
    ], loop=True)

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
