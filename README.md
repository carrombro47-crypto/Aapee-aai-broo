# MyProjects MS - Video Download/Stream API

Advanced video download and streaming API built with Flask, optimized for Vercel, Docker, and self-hosted deployments.

## 🚀 Features

- **Video Streaming/Download** - Stream or download video content (MPD, M3U8, etc.)
- **Token Authentication** - Built-in JWT token support in headers
- **CORS Enabled** - Full CORS support for cross-origin requests
- **Retry Logic** - Automatic retry for transient failures (5xx, timeouts, connection errors)
- **Range Requests** - Support for partial content/byte-range requests
- **Health Checks** - Built-in health endpoint for monitoring
- **Production Ready** - Deployed with Gunicorn + optimized settings
- **Cloud Native** - Vercel, Docker, Kubernetes ready

## 📋 API Endpoints

### `/pw` - Download/Stream Video
**Method:** `GET` | `HEAD` | `OPTIONS`

**Parameters:**
```
url (required)   - Complete video URL (MPD, M3U8, etc.)
token (optional) - JWT token if not in URL
```

**Example:**
```bash
GET /pw?url=https://cdn.example.com/video.mpd&parentId=123&childId=456&token=eyJ0eXAi...

GET /pw?url=https://cdn.example.com/video.m3u8&token=xyz123
```

**Response:**
- Status 200: Video stream (Content-Type: video/mp2t, application/vnd.apple.mpegurl, etc.)
- Status 206: Partial content (Range request)
- Status 400: Missing URL parameter
- Status 502: Upstream fetch failed
- Status 500: Internal error

**CORS Headers:**
```
Access-Control-Allow-Origin: *
Access-Control-Allow-Methods: GET, HEAD, OPTIONS
Access-Control-Expose-Headers: *
```

### `/health` - Health Check
**Method:** `GET`

Returns service status and timestamp for monitoring.

```bash
curl https://yourapp.vercel.app/health
```

### `/` - API Documentation
**Method:** `GET`

Returns full API documentation and usage examples.

## 🛠️ Installation

### Local Development

1. **Clone or create project:**
```bash
mkdir myprojects-ms-video-api && cd myprojects-ms-video-api
```

2. **Create Python virtual environment:**
```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. **Install dependencies:**
```bash
pip install -r requirements.txt
```

4. **Run locally:**
```bash
python pw_main.py
```

Server runs on `http://localhost:5000`

### Docker Deployment

1. **Build image:**
```bash
docker build -t myprojects-ms-video-api .
```

2. **Run container:**
```bash
docker run -p 5000:5000 -e DEBUG=false myprojects-ms-video-api
```

### Vercel Deployment

1. **Install Vercel CLI:**
```bash
npm i -g vercel
```

2. **Deploy:**
```bash
vercel deploy
```

3. **Set environment variables in Vercel Dashboard:**
   - `DEBUG` = `false` (for production)

## 📝 Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `5000` | Server port |
| `DEBUG` | `false` | Enable debug mode |

### Performance Tuning

**Gunicorn Workers (Docker):**
```
--workers 4           # 4 concurrent workers
--max-requests 1000   # Restart after 1000 requests (memory leak prevention)
--timeout 300         # 5-minute timeout per request
```

**Vercel Lambda:**
- Memory: 3008 MB
- Timeout: 300 seconds
- Max Lambda size: 50 MB

## 🔐 Security

- **CORS:** Restricted to necessary methods (GET, HEAD, OPTIONS)
- **Headers:** Server identification headers removed
- **User:** Non-root user in Docker (UID 1000)
- **Timeouts:** Request timeouts prevent hanging
- **Retry Logic:** Prevents denial of service via upstream errors

## 📊 Error Handling

### Upstream Failures
- **2xx, 4xx:** Returned immediately (no retry)
- **5xx:** Retried up to 3 times with backoff
- **Timeout:** Retried up to 3 times
- **Connection Error:** Retried up to 3 times

### Response Codes
```
200 - Success (full file)
206 - Partial Content (range request)
400 - Bad Request (missing URL)
404 - Not Found (invalid endpoint)
502 - Bad Gateway (upstream error)
500 - Internal Server Error
```

## 🧪 Testing

### Test endpoints:

```bash
# Health check
curl https://yourapp.example.com/health

# API info
curl https://yourapp.example.com/

# Download video
curl "https://yourapp.example.com/pw?url=YOUR_VIDEO_URL&token=YOUR_TOKEN" -o video.mp4

# Test CORS
curl -H "Origin: example.com" -H "Access-Control-Request-Method: GET" \
  -H "Access-Control-Request-Headers: Content-Type" \
  -X OPTIONS https://yourapp.example.com/pw
```

## 📚 Architecture

```
Request → Flask App → CORS Middleware
                   ↓
              Token Extraction
                   ↓
         Add Headers + Auth
                   ↓
       Upstream Fetch (with retry)
                   ↓
          Stream to Client
                   ↓
            Response Headers
```

## 🚨 Troubleshooting

### Video not playing
- Check if `url` parameter is correct
- Verify token is valid and not expired
- Check browser console for CORS errors

### 502 Bad Gateway
- Upstream server might be down
- Network might be blocking the request
- Token might be invalid

### Slow performance
- Check upstream server performance
- Monitor network connectivity
- Increase Vercel Lambda memory

### Connection timeout
- Increase `UPSTREAM_TIMEOUT` in code
- Check if upstream is responding

## 📈 Monitoring

Use `/health` endpoint with monitoring services:

```bash
# Uptime monitoring
curl -f https://yourapp.example.com/health || alert

# Prometheus monitoring
curl -s https://yourapp.example.com/health | jq .
```

## 🤝 Contributing

For improvements, bug reports, or feature requests, please reach out to the development team.

## 📄 License

Proprietary - Team Cinderella

## 👥 Support

**Project:** MyProjects MS Video API
**Team:** Team Cinderella
**Telegram:** https://t.me/TeamCinderella

---

**Version:** 1.0.0  
**Last Updated:** 2026-06-21  
**Status:** Production Ready ✅
