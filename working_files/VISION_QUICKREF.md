# Vision Analysis Quick Reference

## Core Functions

### `analyze_image_with_ollama(image_path, prompt_type, custom_prompt)`
Main analysis function. Returns:
```json
{
  "success": true,
  "analysis": "AI description...",
  "model": "mistral-large-3:675b-cloud",
  "tokens_evaluated": 1234,
  "generation_time": 2.5
}
```

### `select_sharpest_image(burst_dir)`
Selects best image from burst (by file size heuristic).

### `analyze_burst_async(burst_id, user_config, burst_dir)`
Async pipeline: selects image → analyzes → saves `vision_analysis.json`

## API Endpoints

### POST /api/vision/analyze
**Use**: Analyze single image on-demand

**Request**:
```json
{
  "image_path": "/data/freeroam/rich/bursts/xxx/image.jpg",
  "prompt_type": "tactical"
}
```

**OR** (base64 upload):
```json
{
  "image_data": "iVBOR...",
  "filename": "temp.jpg",
  "prompt_type": "ocr"
}
```

### GET /api/vision/analyze-burst/<burst_id>
**Use**: Get cached analysis or trigger new analysis

**Response (cached)**:
```json
{
  "status": "cached",
  "analysis": {
    "analyzed_at": "2026-08-18T13:00:30",
    "analyzed_image": "IMG_001.jpg",
    "analysis": {"success": true, "analysis": "..."}
  }
}
```

### GET /api/vision/models
**Use**: List available vision models

### GET /api/vision/test
**Use**: Health check

**Response**:
```json
{
  "status": "operational",
  "ollama_connected": true,
  "vision_model": "polaris-ai:latest",
  "model_available": true,
  "prompt_types": ["general", "tactical", "ocr", "sharpness_check"]
}
```

## Prompt Types

1. **general**: "Analyze this image... main objects, scene context, visible text, activities"
2. **tactical**: "Analyze tactical image... people/vehicles/structures, threats, environmental conditions, recommended actions"
3. **ocr**: "Extract all visible text..."
4. **sharpness_check**: "Evaluate sharpness... Rate 1-10, note blur/focus issues"

## Configuration

```python
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434")
VISION_MODEL = os.environ.get("VISION_MODEL", "polaris-ai:latest")
VISION_TIMEOUT = 60  # seconds
```

## Integration Flow

1. Android sends burst → `/api/burst`
2. Gateway saves images
3. Async task triggered (ThreadPoolExecutor)
4. Selects sharpest image
5. Sends to Ollama for tactical analysis
6. Saves `vision_analysis.json`
7. Client polls `/api/vision/analyze-burst/<id>` for results

## Error Handling

- Timeout (60s): Returns `{"success": false, "error": "Vision analysis timeout"}`
- Ollama offline: Returns `{"success": false, "error": "Connection error..."}`
- Missing image: Returns 404 with error message
- Invalid base64: Returns 400 with decode error

## Testing

```bash
# 1. Check syntax
python3 -m py_compile burst_receiver.py

# 2. Test endpoint (after rebuild)
curl http://localhost:5001/api/vision/test

# 3. Analyze test image
curl -X POST http://localhost:5001/api/vision/analyze \
  -H "Content-Type: application/json" \
  -d '{"image_path": "/path/to/test.jpg", "prompt_type": "general"}'
```
