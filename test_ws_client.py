#!/usr/bin/env python3
"""
WebSocket test client for streaming inference.

Usage:
    python test_ws_client.py <api_key> <model_name> <audio_file.wav>
"""
import asyncio
import sys
import websockets
import json


async def test_streaming(api_key: str, model_name: str, audio_file: str):
    """Test WebSocket streaming inference."""
    uri = f"ws://localhost:8000/v1/ws/infer/{model_name}?api_key={api_key}&version=v1"

    print(f"Connecting to {uri}...")

    async with websockets.connect(uri) as websocket:
        print("Connected!")

        # Wait for ready signal
        ready_msg = await websocket.recv()
        print(f"Ready: {ready_msg}")

        # Read and send audio file
        with open(audio_file, "rb") as f:
            audio_data = f.read()

        print(f"Sending audio file ({len(audio_data)} bytes)...")
        await websocket.send(audio_data)

        # Receive streaming results
        print("\nReceiving results:")
        print("-" * 60)

        while True:
            try:
                message = await asyncio.wait_for(websocket.recv(), timeout=30.0)
                result = json.loads(message)

                if "error" in result:
                    print(f"ERROR: {result['error']}")
                    break

                if "partial" in result:
                    print(f"[Partial] {result['partial']}")
                    if result.get("is_final"):
                        print("\n[Final result received]")
                        break

                elif "result" in result:
                    print(f"[Result] {result['result']}")
                    if result.get("is_final"):
                        break

            except asyncio.TimeoutError:
                print("Timeout waiting for response")
                break

        print("-" * 60)
        print("Stream complete")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: python test_ws_client.py <api_key> <model_name> <audio_file.wav>")
        print("\nExample:")
        print("  python test_ws_client.py your-api-key-here asr test-audio.wav")
        sys.exit(1)

    api_key = sys.argv[1]
    model_name = sys.argv[2]
    audio_file = sys.argv[3]

    asyncio.run(test_streaming(api_key, model_name, audio_file))
