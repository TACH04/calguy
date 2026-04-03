from flask import Flask, render_template, request, jsonify, Response
from flask_cors import CORS
from agent import CalendarAgent
import json
import logging
import asyncio

app = Flask(__name__)
CORS(app)
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

agent = CalendarAgent()

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/chat", methods=["POST"])
def chat():
    """Handles chat messages via Server-Sent Events for live updates."""
    data = request.json
    user_input = data.get("message")
    
    if not user_input:
        return jsonify({"error": "No message provided"}), 400
        
    def generate():
        # Create a new event loop to run the async generator synchronously for Flask
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        gen = agent.chat_step(user_input)
        try:
            while True:
                try:
                    event = loop.run_until_complete(gen.__anext__())
                    yield f"data: {json.dumps(event)}\n\n"
                except StopAsyncIteration:
                    break
        finally:
            loop.close()
            
    return Response(generate(), mimetype="text/event-stream")

@app.route("/api/history", methods=["GET"])
def history():
    return jsonify(agent.get_history())

@app.route("/api/reset", methods=["POST"])
def reset():
    agent.reset()
    return jsonify({"status": "success"})

if __name__ == "__main__":
    print("starting CalGuy Web UI on http://localhost:5000")
    app.run(host='0.0.0.0', port=5000, debug=False)
