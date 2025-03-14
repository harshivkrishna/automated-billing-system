import argparse
import time
from functools import lru_cache
import threading
import json
from queue import Queue

import cv2
from picamera2 import MappedArray, Picamera2
from picamera2.devices.imx500 import IMX500
from flask import Flask, Response
from flask_socketio import SocketIO, emit

# Global variables with locks for thread safety
latest_detections = []
detections_lock = threading.Lock()
frame_queue = Queue(maxsize=10)  # Buffer for streaming frames
PRODUCTS = None

# Global variables for detection control and ingredient aggregation
detection_active = False
# detected_ingredients: key: ingredient label, value: dict with keys: total_weight, count, avg_weight, last_update
detected_ingredients = {}

# Initialize Flask app and SocketIO
app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")


class Detection:
    def __init__(self, coords, category, conf, metadata):
        """Create a Detection object with bounding box, category, and confidence."""
        self.category = category
        self.conf = conf
        try:
            self.box = imx500.convert_inference_coords(coords, metadata, picam2)
            # Ensure box coordinates are valid integers and within frame bounds (640x480)
            self.box = [int(max(0, min(val, 640 if i % 2 == 0 else 480))) for i, val in enumerate(self.box)]
        except Exception as e:
            print(f"Error converting coordinates: {e}")
            # Fallback to scaling raw coordinates assuming normalized [0,1]
            self.box = [int(val * (640 if i % 2 == 0 else 480)) for i, val in enumerate(coords)]


def pre_callback(request):
    """Process detections, draw bounding boxes on the main frame, and queue the frame for streaming."""
    metadata = request.get_metadata()
    np_outputs = imx500.get_outputs(metadata, add_batch=True)
    detections = []
    if np_outputs is not None:
        boxes, scores, classes = np_outputs[0][0], np_outputs[2][0], np_outputs[1][0]
        filtered_detections = [
            (box, score, category)
            for box, score, category in zip(boxes, scores, classes)
            if score >= args.threshold
        ]
        print(f"Found {len(filtered_detections)} detections above threshold")
        for box, score, category in filtered_detections:
            try:
                det = Detection(box, category, score, metadata)
                detections.append(det)
                print(f"Added detection for category {category} with confidence {score:.2f}")
            except Exception as e:
                print(f"Error creating detection: {e}")
    
    # Update global detections for WebSocket (regardless of detection mode)
    with detections_lock:
        global latest_detections
        latest_detections = detections
    
    # Draw detections on the main stream
    with MappedArray(request, "main") as m:
        labels = get_labels()
        for detection in detections:
            x, y, w, h = detection.box
            label = f"{labels[int(detection.category)]} ({detection.conf:.2f})"
            cv2.putText(
                m.array,
                label,
                (x + 5, y + 15),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0, 255, 0),
                2,
            )
            cv2.rectangle(m.array, (x, y), (x + w, y + h), (0, 255, 0), 2)
        if not frame_queue.full():
            frame_queue.put(m.array.copy())


@lru_cache
def get_labels():
    """Load and cache labels from the labels file."""
    try:
        with open(args.labels, 'r') as f:
            labels = [line.strip() for line in f.readlines()]
        print(f"Loaded {len(labels)} labels from {args.labels}")
        return labels
    except Exception as e:
        print(f"Error loading labels: {e}")
        return []


def generate_frames():
    """Generate MJPEG frames for video streaming from the queue."""
    while True:
        if not frame_queue.empty():
            frame = frame_queue.get()
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            ret, buffer = cv2.imencode('.jpg', rgb_frame)
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
        else:
            time.sleep(0.01)


@app.route('/video_feed')
def video_feed():
    """Serve the MJPEG video stream."""
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route('/')
def index():
    """Serve the HTML page for the website with video feed and dashboard."""
    return '''
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Smart Checkout - Cooking Mode</title>
        <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
        <style>
            body { font-family: Arial, sans-serif; }
            #container {
                display: flex;
                flex-direction: row;
                justify-content: space-around;
            }
            #left-pane, #right-pane {
                flex: 1;
                padding: 20px;
            }
            #video-feed { width: 640px; height: 480px; }
            table {
                width: 100%;
                border-collapse: collapse;
            }
            th, td {
                border: 1px solid #ccc;
                padding: 8px;
                text-align: left;
            }
            button {
                padding: 10px 20px;
                margin: 10px;
                font-size: 16px;
            }
        </style>
    </head>
    <body>
        <h1>Smart Checkout - Cooking Mode</h1>
        <div id="container">
            <div id="left-pane">
                <h2>Camera Feed</h2>
                <img id="video-feed" src="/video_feed" alt="Camera Feed">
                <div>
                    <button id="start-btn">Start Detection</button>
                    <button id="stop-btn">Stop Detection</button>
                </div>
            </div>
            <div id="right-pane">
                <h2>Dashboard</h2>
                <table id="dashboard-table">
                    <thead>
                        <tr>
                            <th>Product</th>
                            <th>Quantity</th>
                            <th>Price</th>
                        </tr>
                    </thead>
                    <tbody>
                        <!-- Rows will be updated in real time -->
                    </tbody>
                </table>
            </div>
        </div>
        <script>
            const socket = io();
            const startBtn = document.getElementById('start-btn');
            const stopBtn = document.getElementById('stop-btn');
            const tableBody = document.getElementById('dashboard-table').getElementsByTagName('tbody')[0];

            startBtn.addEventListener('click', function() {
                socket.emit('start_detection');
            });

            stopBtn.addEventListener('click', function() {
                socket.emit('stop_detection');
            });

            socket.on('detection_update', function(data) {
                tableBody.innerHTML = '';
                data.products.forEach(item => {
                    const row = document.createElement('tr');
                    const productCell = document.createElement('td');
                    productCell.textContent = item.name;
                    const quantityCell = document.createElement('td');
                    quantityCell.textContent = item.quantity;
                    const priceCell = document.createElement('td');
                    priceCell.textContent = `$${(item.price * item.quantity).toFixed(2)}`;
                    row.appendChild(productCell);
                    row.appendChild(quantityCell);
                    row.appendChild(priceCell);
                    tableBody.appendChild(row);
                });
            });
        </script>
    </body>
    </html>
    '''


@socketio.on('start_detection')
def handle_start_detection():
    global detection_active, detected_ingredients
    detection_active = True
    detected_ingredients = {}  # Reset accumulated data for new detection
    print("Detection started.")


@socketio.on('stop_detection')
def handle_stop_detection():
    global detection_active
    detection_active = False
    print("Detection stopped.")


def emit_detections():
    """Send detection updates via WebSocket, processing new detections and updating the dashboard.
    
    While detection is active:
      - Track product name, quantity, and price
      - Increment quantity when product is detected again after threshold delay
    """
    weight_update_threshold = 2.0  # seconds
    while True:
        current_time = time.time()
        # Copy the current detections for processing
        with detections_lock:
            current_detections = list(latest_detections)
        
        if detection_active:
            # Process detections: update detected_ingredients with quantity
            for det in current_detections:
                label = get_labels()[int(det.category)]
                if label not in detected_ingredients:
                    detected_ingredients[label] = {
                        "quantity": 1,
                        "last_update": current_time,
                    }
                else:
                    # Update only if a certain time has passed to avoid over-counting
                    if current_time - detected_ingredients[label]["last_update"] >= weight_update_threshold:
                        d = detected_ingredients[label]
                        d["quantity"] += 1
                        d["last_update"] = current_time

            # Prepare data for emission during active detection
            data_list = []
            for label, info in detected_ingredients.items():
                product_info = PRODUCTS.get(label, {})
                price = product_info.get("price", 0)
                data_list.append({
                    "name": label,
                    "quantity": info["quantity"],
                    "price": price
                })
            socketio.emit('detection_update', {"products": data_list})
        else:
            # When detection is stopped, emit final data
            data_list = []
            for label, info in detected_ingredients.items():
                product_info = PRODUCTS.get(label, {})
                price = product_info.get("price", 0)
                data_list.append({
                    "name": label,
                    "quantity": info["quantity"],
                    "price": price
                })
            socketio.emit('detection_update', {"products": data_list})
        time.sleep(0.1)


def add_test_detections():
    """Add fake detections for testing visualization.
    
    Cycles through different product categories to simulate scanning multiple products.
    """
    category = 0  # Start with first product
    max_category = len(get_labels()) - 1  # Get number of available products
    
    while True:
        with detections_lock:
            global latest_detections
            # Create a test detection with current category
            test_detection = Detection([100, 100, 200, 150], category, 0.95, {"ScalerCrop": (480, 640)})
            latest_detections = [test_detection]
            label = get_labels()[category]
            print(f"Added test detection for {label}")
            
            # Move to next category, loop back to 0 if at end
            category = (category + 1) % (max_category + 1)
            
        time.sleep(2)


def get_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=str, required=True, help="Path to the model")
    parser.add_argument("--fps", type=int, default=15, help="Frames per second")
    parser.add_argument("--threshold", type=float, default=0.2, help="Detection threshold")
    parser.add_argument("--labels", type=str, default="assets/labels.txt", help="Path to labels file")
    parser.add_argument("--products", type=str, default="products.json", help="Path to product details JSON")
    parser.add_argument("--test-mode", action="store_true", help="Run with test detections")
    return parser.parse_args()


if __name__ == "__main__":
    args = get_args()

    # Initialize IMX500 and Picamera2
    imx500 = IMX500(args.model)
    imx500.show_network_fw_progress_bar()
    picam2 = Picamera2()
    config = picam2.create_video_configuration(
        main={"size": (640, 480)},
        lores={"size": (640, 480)},
        controls={"FrameRate": args.fps}
    )
    picam2.pre_callback = pre_callback

    # Load product details (ensure products.json is updated accordingly)
    with open(args.products, 'r') as f:
        PRODUCTS = json.load(f)

    # Start the camera
    picam2.start(config, show_preview=True)

    # Start test detections if enabled
    if args.test_mode:
        threading.Thread(target=add_test_detections, daemon=True).start()
        print("Running in test mode with fake detections")

    # Start WebSocket emitter for updating dashboard
    threading.Thread(target=emit_detections, daemon=True).start()

    # Run Flask app with Socket.IO
    socketio.run(app, host='0.0.0.0', port=5000)
