"""
CameraCapture - 摄像头采集组件（人脸检测 + 情绪识别）

设计文档：docs/camera-emotion-design.md

一条流水线解决两个问题：
  1. 人在不在电脑前（人脸检测，每 5s）
  2. 用户什么情绪（情绪识别，每 30s）

分频策略：
  - 每次 _capture_impl() 都做人脸检测（~3ms，极轻量）
  - 每 N 次才做一次情绪识别（~30ms，按需触发）
  - 无人脸时跳过情绪推理，零额外开销

隐私保证：
  - 帧读取后立即处理，处理完立即丢弃
  - 不存储任何图像数据
  - 只输出文字标签（emotion + confidence + user_present）

依赖：
  - opencv-python-headless >= 4.8
  - onnxruntime >= 1.16（仅情绪识别需要）
  - 人脸检测使用 OpenCV 内置 Haar Cascade，无需额外模型文件
  - 情绪识别模型：emotion-ferplus-8.onnx（可选，缺失时仅做存在检测）
"""
import logging
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from collector.base import BaseCaptureComponent

logger = logging.getLogger("navi.collector.camera")

# 情绪标签（emotion-ferplus-8.onnx 输出 8 类）
EMOTION_LABELS = ["neutral", "happy", "surprise", "sad", "angry", "disgust", "fear", "contempt"]


class CameraCapture(BaseCaptureComponent):
    """
    摄像头采集组件 — 人脸存在检测 + 情绪识别

    遵循 BaseCaptureComponent 模式：
    - 独立后台线程运行
    - 回调机制输出结果
    - 统计追踪 + 优雅停止
    """

    def __init__(
        self,
        capture_interval: float = 5.0,
        emotion_every_n: int = 6,
        camera_index: int = 0,
        model_dir: str = "models",
        confidence_threshold: float = 0.7,
    ):
        """
        Args:
            capture_interval: 人脸检测频率（秒），默认 5s
            emotion_every_n: 每 N 次人脸检测做 1 次情绪识别，默认 6（即 30s）
            camera_index: 摄像头设备索引，0 = 默认摄像头
            model_dir: ONNX 模型文件目录
            confidence_threshold: 人脸检测置信度阈值
        """
        super().__init__(name="CameraCapture", capture_interval=capture_interval)
        self._camera_index = camera_index
        self._model_dir = Path(model_dir)
        self._confidence_threshold = confidence_threshold
        self._emotion_every_n = emotion_every_n

        # 运行时状态
        self._cap = None  # cv2.VideoCapture
        self._face_cascade = None  # OpenCV Haar Cascade 人脸检测
        self._emotion_session = None  # ONNX 情绪识别
        self._tick = 0  # 采集计数器（用于分频）

        # 最近状态（供外部查询）
        self._last_present: bool = False
        self._last_emotion: Optional[str] = None
        self._last_emotion_confidence: float = 0.0

        # 平滑处理：最近 5 次人脸检测结果取多数
        self._presence_buffer: deque = deque(maxlen=5)

    def _start_impl(self) -> bool:
        """初始化摄像头 + 加载人脸检测器 + 可选加载情绪模型"""
        try:
            import cv2
        except ImportError:
            logger.error("❌ opencv-python-headless 未安装，请运行: uv add opencv-python-headless")
            return False

        # 1. 初始化 Haar Cascade 人脸检测器（OpenCV 内置，无需额外模型文件）
        self._face_cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )
        if self._face_cascade.empty():
            logger.error("❌ Haar Cascade 人脸检测器加载失败")
            return False
        logger.info("✅ 人脸检测器加载成功 (Haar Cascade)")

        # 2. 打开摄像头
        self._cap = cv2.VideoCapture(self._camera_index, cv2.CAP_DSHOW)
        if not self._cap.isOpened():
            logger.error(f"❌ 无法打开摄像头 (index={self._camera_index})")
            return False

        # 设置较低分辨率以减少开销
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self._cap.set(cv2.CAP_PROP_FPS, 5)  # 最低帧率，省资源

        # 摄像头预热：第一帧通常曝光不正确，丢弃几帧
        for _ in range(3):
            self._cap.read()

        # 3. 可选加载情绪识别 ONNX 模型
        emotion_model_path = self._model_dir / "emotion-ferplus-8.onnx"

        if emotion_model_path.exists():
            try:
                import onnxruntime as ort

                sess_options = ort.SessionOptions()
                sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                sess_options.intra_op_num_threads = 1  # 单线程，不抢 CPU
                sess_options.inter_op_num_threads = 1

                self._emotion_session = ort.InferenceSession(
                    str(emotion_model_path), sess_options, providers=["CPUExecutionProvider"]
                )
                logger.info(f"✅ 情绪识别模型加载成功: {emotion_model_path.name}")
            except ImportError:
                logger.warning("⚠️ onnxruntime 未安装，情绪识别功能不可用")
                self._emotion_session = None
            except Exception as e:
                logger.warning(f"⚠️ 情绪识别模型加载失败: {e}，情绪识别功能不可用")
                self._emotion_session = None
        else:
            logger.warning(
                f"⚠️ 情绪识别模型缺失: {emotion_model_path}，仅进行人脸存在检测"
            )
            self._emotion_session = None

        self._tick = 0
        logger.info(
            f"📹 摄像头已打开 (index={self._camera_index}, "
            f"人脸检测间隔={self._capture_interval}s, "
            f"情绪识别间隔={self._capture_interval * self._emotion_every_n}s)"
        )
        return True

    def _capture_impl(self) -> List[Dict]:
        """
        单次采集：人脸检测 + 条件性情绪识别

        Returns:
            [{
                "user_present": bool,
                "face_count": int,
                "emotion": str | None,
                "emotion_confidence": float | None,
                "captured_at": str,
            }]
        """
        import cv2

        self._tick += 1

        # 1. 读取一帧
        ret, frame = self._cap.read()
        if not ret or frame is None:
            logger.warning("📹 摄像头读帧失败")
            return [self._make_result(user_present=False, face_count=0)]

        # 2. 人脸检测
        faces = self._detect_faces(frame)
        face_count = len(faces)

        # 平滑处理（改进：当前帧有人脸直接判定在，只有连续无人脸才判定离开）
        self._presence_buffer.append(face_count > 0)
        # 最近 5 次中有 2 次以上检测到人脸 → 在；或者当前帧直接检测到 → 在
        user_present = face_count > 0 or sum(self._presence_buffer) >= 2

        # 3. 情绪识别（分频 + 有人脸才跑）
        emotion = None
        emotion_confidence = None

        if face_count > 0 and self._tick % self._emotion_every_n == 0:
            emotion, emotion_confidence = self._classify_emotion(frame, faces[0])
            if emotion:
                self._last_emotion = emotion
                self._last_emotion_confidence = emotion_confidence

        # 4. 帧已不再需要，Python GC 会回收
        del frame

        # 更新状态
        self._last_present = user_present

        # 5. 每次都打印日志（摄像头采集本身间隔 5s，不会刷屏）
        emotion_str = ""
        if emotion:
            emotion_str = f" | 🎭 情绪:{emotion}({emotion_confidence:.0%})"
        elif self._last_emotion:
            emotion_str = f" | 🎭 上次:{self._last_emotion}"

        logger.info(
            f"📹 [#{self._tick}] 人脸:{face_count} "
            f"用户:{'✅在' if user_present else '❌离'}"
            f"{emotion_str}"
        )

        return [self._make_result(
            user_present=user_present,
            face_count=face_count,
            emotion=emotion,
            emotion_confidence=emotion_confidence,
        )]

    def _detect_faces(self, frame: np.ndarray) -> List[Tuple[int, int, int, int]]:
        """
        OpenCV Haar Cascade 人脸检测

        Args:
            frame: BGR 图像 (H, W, 3)

        Returns:
            [(x1, y1, x2, y2), ...] 人脸框列表
        """
        import cv2

        if self._face_cascade is None:
            return []

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        # 参数调优：minNeighbors=3 + minSize=30 在笔记本摄像头上更可靠
        detections = self._face_cascade.detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=3, minSize=(30, 30)
        )

        faces = []
        for (x, y, w, h) in detections:
            faces.append((x, y, x + w, y + h))

        return faces

    def _classify_emotion(
        self, frame: np.ndarray, face_box: Tuple[int, int, int, int]
    ) -> Tuple[Optional[str], Optional[float]]:
        """
        对裁剪的人脸区域做情绪分类

        Args:
            frame: 原始 BGR 图像
            face_box: (x1, y1, x2, y2)

        Returns:
            (emotion_label, confidence) 或 (None, None)
        """
        import cv2

        if self._emotion_session is None:
            return None, None

        x1, y1, x2, y2 = face_box

        # 裁剪人脸 + 添加一点 padding
        pad = int((x2 - x1) * 0.1)
        h, w = frame.shape[:2]
        fx1 = max(0, x1 - pad)
        fy1 = max(0, y1 - pad)
        fx2 = min(w, x2 + pad)
        fy2 = min(h, y2 + pad)
        face_crop = frame[fy1:fy2, fx1:fx2]

        if face_crop.size == 0:
            return None, None

        # 预处理：灰度 → resize to 64x64 → normalize（emotion-ferplus-8 输入格式）
        gray = cv2.cvtColor(face_crop, cv2.COLOR_BGR2GRAY)
        resized = cv2.resize(gray, (64, 64))
        normalized = resized.astype(np.float32) / 255.0

        # emotion-ferplus-8.onnx 输入: Input3, shape [1, 1, 64, 64]
        input_name = self._emotion_session.get_inputs()[0].name
        img_input = normalized.reshape(1, 1, 64, 64)

        # 推理
        try:
            outputs = self._emotion_session.run(None, {input_name: img_input})
            probs = outputs[0][0]

            # softmax（如果模型输出不是概率）
            if probs.max() > 1.0 or probs.min() < 0.0:
                exp_vals = np.exp(probs - np.max(probs))
                probs = exp_vals / exp_vals.sum()

            idx = int(np.argmax(probs))
            confidence = float(probs[idx])
            emotion = EMOTION_LABELS[idx] if idx < len(EMOTION_LABELS) else "neutral"
            return emotion, confidence
        except Exception as e:
            logger.debug(f"情绪推理失败: {e}")
            return None, None

    def _make_result(
        self,
        user_present: bool,
        face_count: int,
        emotion: Optional[str] = None,
        emotion_confidence: Optional[float] = None,
    ) -> Dict:
        """构造标准输出 dict"""
        return {
            "user_present": user_present,
            "face_count": face_count,
            "emotion": emotion,
            "emotion_confidence": emotion_confidence,
            "captured_at": datetime.now().isoformat(),
        }

    def _stop_impl(self, graceful: bool = True) -> bool:
        """释放摄像头和模型资源"""
        if self._cap is not None:
            self._cap.release()
            self._cap = None
            logger.info("📹 摄像头已释放")

        self._face_cascade = None
        self._emotion_session = None
        self._presence_buffer.clear()
        return True

    # ── 外部查询接口 ──

    @property
    def user_present(self) -> bool:
        """当前用户是否在电脑前"""
        return self._last_present

    @property
    def current_emotion(self) -> Optional[str]:
        """当前情绪标签"""
        return self._last_emotion

    @property
    def current_emotion_confidence(self) -> float:
        """当前情绪置信度"""
        return self._last_emotion_confidence

    def get_stats(self) -> Dict:
        """扩展统计信息"""
        stats = super().get_stats()
        stats.update({
            "user_present": self._last_present,
            "current_emotion": self._last_emotion,
            "emotion_confidence": self._last_emotion_confidence,
            "camera_index": self._camera_index,
        })
        return stats
