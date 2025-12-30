import cv2
import numpy as np
from collections import defaultdict, OrderedDict
from scipy.spatial import distance as dist

class CentroidTracker:
    def __init__(self, max_disappeared=50):
        self.next_object_id = 0
        self.objects = OrderedDict()
        self.disappeared = OrderedDict()
        self.max_disappeared = max_disappeared

    def register(self, centroid):
        self.objects[self.next_object_id] = centroid
        self.disappeared[self.next_object_id] = 0
        self.next_object_id += 1

    def deregister(self, object_id):
        del self.objects[object_id]
        del self.disappeared[object_id]

    def update(self, rects, frame_width):
        if len(rects) == 0:
            for object_id in list(self.disappeared.keys()):
                self.disappeared[object_id] += 1
                if self.disappeared[object_id] > self.max_disappeared:
                    self.deregister(object_id)
            return self.objects

        input_centroids = np.zeros((len(rects), 2), dtype="int")
        for (i, (x, y, w, h)) in enumerate(rects):
            c_x = int(x + w / 2.0)
            c_y = int(y + h / 2.0)
            input_centroids[i] = (c_x, c_y)

        if len(self.objects) == 0:
            for i in range(len(input_centroids)):
                self.register(input_centroids[i])
        else:
            object_ids = list(self.objects.keys())
            object_centroids = list(self.objects.values())

            D = dist.cdist(np.array(object_centroids), input_centroids)
            rows = D.min(axis=1).argsort()
            cols = D.argmin(axis=1)[rows]

            used_rows = set()
            used_cols = set()

            for (row, col) in zip(rows, cols):
                if row in used_rows or col in used_cols:
                    continue
                
                if D[row, col] > frame_width * 0.3: # 距離のしきい値を少し緩和
                    continue

                object_id = object_ids[row]
                self.objects[object_id] = input_centroids[col]
                self.disappeared[object_id] = 0
                used_rows.add(row)
                used_cols.add(col)

            unused_rows = set(range(D.shape[0])).difference(used_rows)
            unused_cols = set(range(D.shape[1])).difference(used_cols)

            if D.shape[0] >= D.shape[1]:
                for row in unused_rows:
                    object_id = object_ids[row]
                    self.disappeared[object_id] += 1
                    if self.disappeared[object_id] > self.max_disappeared:
                        self.deregister(object_id)
            else:
                for col in unused_cols:
                    self.register(input_centroids[col])
        return self.objects

def detect_performances_by_motion(video_path, config):
    print("動きの検出による演奏区間の検出を開始します...")
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"エラー: 動画ファイル '{video_path}' を開けませんでした。")
        return []

    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    
    max_frames = int(config['max_seconds_to_process'] * fps) if config['max_seconds_to_process'] is not None else float('inf')

    # --- ゾーンと状態の管理 ---
    LEFT_ZONE_END = width * config['left_zone_end_percent']
    CENTER_ZONE_END = width * config['center_zone_end_percent']
    stage_status = 'empty' # 'empty' または 'occupied'
    performance_start_time = 0
    performance_segments = []

    # --- CVオブジェクト ---
    back_sub = cv2.createBackgroundSubtractorMOG2(history=500, varThreshold=config['mog2_threshold'], detectShadows=False)
    ct = CentroidTracker(max_disappeared=int(fps * 3)) # 静止時間を考慮し、少し長めに設定

    # --- 以前のフレームのオブジェクト位置を追跡 ---
    # IDごとのゾーン履歴を保持
    last_known_zones = defaultdict(lambda: 'unknown')
    
    frame_number = 0
    while cap.isOpened() and frame_number < max_frames:
        ret, frame = cap.read()
        if not ret:
            break

        fg_mask = back_sub.apply(frame)
        thresh = cv2.threshold(fg_mask, 128, 255, cv2.THRESH_BINARY)[1]
        thresh = cv2.erode(thresh, None, iterations=2)
        thresh = cv2.dilate(thresh, None, iterations=4)
        
        contours, _ = cv2.findContours(thresh.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        rects = [cv2.boundingRect(c) for c in contours if cv2.contourArea(c) > config['min_contour_area']]
        tracked_centroids = ct.update(rects, width)

        current_zones = {}
        for (object_id, centroid) in tracked_centroids.items():
            if centroid[0] < LEFT_ZONE_END: zone = 'left'
            elif centroid[0] < CENTER_ZONE_END: zone = 'center'
            else: zone = 'right'
            current_zones[object_id] = zone
            
            # --- 新しい状態管理ロジック ---
            last_zone = last_known_zones[object_id]
            current_zone = current_zones[object_id]
            
            # 舞台が「空」の場合：入場を検出
            if stage_status == 'empty':
                if last_zone == 'left' and current_zone == 'center':
                    stage_status = 'occupied'
                    performance_start_time = frame_number / fps
                    print(f"ID {object_id} の入場を検出。演奏開始とみなします: {performance_start_time:.2f}秒")
            
            # 舞台が「演奏中」の場合：退場を検出
            elif stage_status == 'occupied':
                if last_zone == 'center' and current_zone == 'left':
                    end_time = frame_number / fps
                    print(f"ID {object_id} の退場を検出。演奏終了とみなします: {end_time:.2f}秒")
                    performance_segments.append((performance_start_time, end_time))
                    stage_status = 'empty' # 舞台をリセット

            # ゾーン履歴を更新
            last_known_zones[object_id] = current_zone

        # --- 描画処理 (変更なし) ---
        if config['show_video']:
            cv2.line(frame, (int(LEFT_ZONE_END), 0), (int(LEFT_ZONE_END), height), (255, 0, 0), 2)
            cv2.line(frame, (int(CENTER_ZONE_END), 0), (int(CENTER_ZONE_END), height), (255, 0, 0), 2)
            for x, y, w, h in rects:
                cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
            for (object_id, centroid) in tracked_centroids.items():
                 cv2.putText(frame, f"ID {object_id}", (centroid[0] - 10, centroid[1] - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
                 cv2.circle(frame, (centroid[0], centroid[1]), 4, (0, 0, 255), -1)
            
            new_width = 960
            ratio = new_width / width
            resized_frame = cv2.resize(frame, (new_width, int(height * ratio)))
            cv2.imshow("Motion Detection", resized_frame)

            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

        frame_number += 1
        if not config['show_video'] and frame_number > 0 and frame_number % 100 == 0:
            print(f"  ... フレーム {frame_number} を処理中 ({frame_number / fps:.2f}秒地点)")
    
    cap.release()
    if config['show_video']:
        cv2.destroyAllWindows()
    
    final_segments = [seg for seg in performance_segments if (seg[1] - seg[0]) >= config['min_duration_seconds']]
    print(f"動きの区間を {len(performance_segments)}件検出、うち{len(final_segments)}件が指定長を満たしています。")
    return sorted(final_segments, key=lambda x: x[0])

if __name__ == '__main__':
    detection_config = {
        'max_seconds_to_process': 480,
        'min_duration_seconds': 30,
        'show_video': True,
        'mog2_threshold': 40, 
        'min_contour_area': 3000,
        'left_zone_end_percent': 0.25,
        'center_zone_end_percent': 0.55
    }
    
    video_file = 'input/00002.MTS'
    segments = detect_performances_by_motion(video_file, detection_config)
    
    if segments:
        print("\n--- 検出結果 ---")
        for i, (start, end) in enumerate(segments):
            print(f"演奏 {i+1}: 開始 {start:.2f}秒 - 終了 {end:.2f}秒 (長さ: {end-start:.2f}秒)")
    else:
        print("\n--- 検出結果 ---")
        print("指定された条件に合う演奏区間は見つかりませんでした。")
