"""
MyProjects MS - Video Download/Stream API for Vercel
Handles /pw route for streaming video content with CORS & token support
"""

import os
import logging
import time
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse, parse_qsl
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from flask import Flask, request, Response, jsonify
from flask_cors import CORS
from datetime import datetime

# Global session for cookie persistence across requests
_session = None

def get_session():
    """Get or create a requests session with retry strategy."""
    global _session
    if _session is None:
        _session = requests.Session()
        # Retry strategy for transient failures
        retry_strategy = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET", "HEAD"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        _session.mount("http://", adapter)
        _session.mount("https://", adapter)
    return _session

# ═══════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*", "methods": ["GET", "HEAD", "OPTIONS"], "allow_headers": ["*"]}})

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
UPSTREAM_TIMEOUT = 30
UPSTREAM_MAX_RETRIES = 3
CHUNK_SIZE = 8192

UPSTREAM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "video",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "cross-site",
}

# ═══════════════════════════════════════════════════════════════════════════
# UTILITY FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════

def extract_token_from_url(url: str) -> str:
    """Extract token parameter from URL if present."""
    try:
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        if 'token' in params:
            return params['token'][0]
    except Exception:
        pass
    return ""


def fetch_upstream_with_retry(url: str, token: str = ""):
    """
    Fetch upstream using persistent session (for CloudFront cookies).
    - Preserves ALL query parameters and cookies
    - Handles CloudFront signed URLs properly
    - 2xx, 4xx: Final (don't retry)
    - 5xx, 403, 401: Retry with backoff
    """
    session = get_session()
    headers = dict(UPSTREAM_HEADERS)
    
    # Add token to Authorization header if provided
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["X-Auth-Token"] = token
    
    # Forward ALL client cookies (critical for CloudFront)
    client_cookies = {}
    if request.headers.get("Cookie"):
        for cookie_part in request.headers.get("Cookie", "").split(";"):
            if "=" in cookie_part:
                k, v = cookie_part.strip().split("=", 1)
                client_cookies[k.strip()] = v.strip()
        headers["Cookie"] = request.headers["Cookie"]
    
    # Forward CloudFront & security headers
    cf_headers = [
        "CloudFront-Viewer-Country",
        "CloudFront-Viewer-Country-Region", 
        "CloudFront-Viewer-Country-Region-Name",
        "CloudFront-Viewer-Http-Version",
        "CloudFront-Viewer-Latitude",
        "CloudFront-Viewer-Longitude",
        "CloudFront-Viewer-Metro-Code",
        "CloudFront-Viewer-Time-Zone",
        "User-Agent",
        "Referer"
    ]
    for cf_header in cf_headers:
        if request.headers.get(cf_header):
            headers[cf_header] = request.headers[cf_header]
    
    # Add Range header if client requested partial content
    if request.headers.get("Range"):
        headers["Range"] = request.headers["Range"]
    
    # Enhanced user agent for compatibility
    headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    
    # Streaming headers
    headers["Accept-Ranges"] = "bytes"
    headers["Cache-Control"] = "no-cache"
    headers["Pragma"] = "no-cache"
    
    last_exc = None
    last_status = None
    last_response = None
    
    for attempt in range(UPSTREAM_MAX_RETRIES + 1):
        try:
            logger.info(f"Fetch attempt {attempt + 1}/{UPSTREAM_MAX_RETRIES + 1}: {url[:100]}...")
            
            # Use session for cookie persistence
            response = session.get(
                url,
                headers=headers,
                timeout=UPSTREAM_TIMEOUT,
                allow_redirects=True,
                stream=True,
                verify=True,
                cookies=client_cookies if client_cookies else None
            )
            
            last_response = response
            
            # Log response details
            logger.info(f"Response status: {response.status_code}, CT: {response.headers.get('Content-Type', 'unknown')}, Size: {response.headers.get('Content-Length', 'unknown')}")
            
            # Log any Set-Cookie headers (important for CloudFront)
            if response.headers.get("Set-Cookie"):
                logger.info(f"Set-Cookie received: {response.headers.get('Set-Cookie')[:100]}")
            
            # Success: 2xx
            if 200 <= response.status_code < 300:
                logger.info(f"✅ Status {response.status_code} (success)")
                return response
            
            # Client errors: 4xx (except 403/401)
            if 400 <= response.status_code < 500:
                if response.status_code in [401, 403]:
                    # Auth errors - might be temporary, retry
                    logger.warning(f"Auth error {response.status_code}, retrying (attempt {attempt + 1}/{UPSTREAM_MAX_RETRIES + 1})...")
                    last_status = response.status_code
                    time.sleep(0.5 * (attempt + 1))
                    continue
                else:
                    # Other 4xx - don't retry
                    logger.warning(f"✗ Status {response.status_code} (client error, no retry)")
                    return response
            
            # Server errors: 5xx
            if response.status_code >= 500:
                last_status = response.status_code
                logger.warning(f"Server error {response.status_code}, retrying (attempt {attempt + 1}/{UPSTREAM_MAX_RETRIES + 1})...")
                time.sleep(0.5 * (attempt + 1))
                continue
                
        except requests.exceptions.Timeout as e:
            logger.warning(f"Timeout attempt {attempt + 1}, retrying...")
            last_exc = f"Timeout"
            time.sleep(0.5 * (attempt + 1))
            continue
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"Connection error attempt {attempt + 1}, retrying: {e}")
            last_exc = "ConnectionError"
            time.sleep(0.5 * (attempt + 1))
            continue
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            last_exc = str(e)
            break
    
    # All retries exhausted
    error_msg = f"Fetch failed after {UPSTREAM_MAX_RETRIES + 1} attempts"
    if last_status:
        error_msg += f" (last status: {last_status})"
    if last_exc:
        error_msg += f" ({last_exc})"
    
    logger.error(error_msg)
    
    # Return last response if we have one
    if last_response:
        logger.warning(f"Returning last response: {last_response.status_code}")
        return last_response
    
    raise Exception(error_msg)


# ═══════════════════════════════════════════════════════════════════════════
# FLASK ROUTES
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/pw', methods=['GET', 'HEAD', 'OPTIONS'])
def download_video():
    """
    Main video download/stream endpoint with enhanced cookie & auth handling.
    
    Query Parameters:
    - url (required): Complete video URL (MPD, m3u8, etc.)
    - token (optional): JWT token for authentication
    
    Example:
    /pw?url=https://domain.com/path/master.mpd&parentId=123&childId=456&token=xyz
    """
    
    # Handle CORS preflight
    if request.method == 'OPTIONS':
        return '', 204
    
    try:
        # Get video URL from query params
        video_url = request.args.get('url', '').strip()
        
        if not video_url:
            logger.warning("No URL provided to /pw endpoint")
            return jsonify({
                "error": "Missing 'url' query parameter",
                "example": "/pw?url=https://domain.com/video.mpd&token=xyz"
            }), 400
        
        # Log request (sanitize for privacy)
        url_preview = video_url[:120] + ("..." if len(video_url) > 120 else "")
        logger.info(f"Download request: {url_preview}")
        
        # Extract token from URL or query params
        token = request.args.get('token', '').strip()
        if not token:
            token = extract_token_from_url(video_url)
        
        if token:
            logger.info(f"Token auth detected (length: {len(token)})")
        
        # Fetch upstream with retry
        try:
            logger.info("Initiating upstream fetch...")
            upstream = fetch_upstream_with_retry(video_url, token)
            logger.info(f"Upstream response: {upstream.status_code}")
        except Exception as e:
            logger.error(f"Upstream fetch failed: {e}", exc_info=True)
            return jsonify({
                "error": "Failed to fetch video from upstream",
                "details": str(e),
                "type": type(e).__name__
            }), 502
        
        # Return upstream error response to client
        if upstream.status_code >= 400:
            logger.warning(f"Upstream returned error: {upstream.status_code}")
            
            # Log error content for debugging (limit to 500 chars)
            try:
                error_preview = upstream.text[:500] if hasattr(upstream, 'text') else str(upstream.content[:500])
                logger.error(f"Error response: {error_preview}")
            except:
                pass
            
            # Forward upstream error exactly as-is to client
            return upstream.content, upstream.status_code, dict(upstream.headers)
        
        # Success - stream response to client
        logger.info(f"Streaming success (status {upstream.status_code}, CT: {upstream.headers.get('Content-Type', 'unknown')})")
        
        def generate():
            """Stream content in chunks."""
            bytes_sent = 0
            try:
                for chunk in upstream.iter_content(chunk_size=CHUNK_SIZE):
                    if chunk:
                        bytes_sent += len(chunk)
                        yield chunk
                logger.info(f"Stream complete: {bytes_sent} bytes")
            except GeneratorExit:
                logger.info(f"Client disconnected after {bytes_sent} bytes")
            except Exception as e:
                logger.error(f"Stream error: {e}")
                raise
            finally:
                try:
                    upstream.close()
                except:
                    pass
        
        # Prepare response headers
        response_headers = dict(upstream.headers)
        
        # CORS headers - allow all origins
        response_headers["Access-Control-Allow-Origin"] = "*"
        response_headers["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
        response_headers["Access-Control-Allow-Credentials"] = "true"
        response_headers["Access-Control-Expose-Headers"] = "Content-Length, Content-Range, Content-Type, Accept-Ranges"
        
        # Streaming & caching headers
        response_headers["Accept-Ranges"] = "bytes"
        response_headers["Cache-Control"] = "public, max-age=3600"
        
        # Remove sensitive server identification headers
        sensitive_headers = ["Server", "X-Amzn-RequestId", "X-Cache", "Via", "X-Frame-Options", "X-XSS-Protection"]
        for header in sensitive_headers:
            response_headers.pop(header, None)
        
        # Determine and set content type based on URL
        content_type = upstream.headers.get('Content-Type', 'application/octet-stream')
        if 'mpd' in video_url.lower():
            content_type = 'application/dash+xml'
        elif 'm3u8' in video_url.lower() or 'playlist' in video_url.lower():
            content_type = 'application/vnd.apple.mpegurl'
        
        logger.info(f"Sending with Content-Type: {content_type}, Size: {response_headers.get('Content-Length', 'unknown')}")
        
        return Response(
            generate(),
            status=upstream.status_code,
            headers=response_headers,
            mimetype=content_type
        )
    
    except Exception as e:
        logger.error(f"Unhandled error in /pw endpoint: {e}", exc_info=True)
        return jsonify({
            "error": "Internal server error",
            "details": str(e),
            "type": type(e).__name__
        }), 500


@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint for monitoring."""
    return jsonify({
        "status": "healthy",
        "service": "MyProjects MS Video API",
        "timestamp": datetime.now().isoformat()
    }), 200


@app.route('/', methods=['GET'])
def index():
    """Root endpoint with API documentation."""
    return jsonify({
        "service": "MyProjects MS - Video Download/Stream API",
        "version": "1.0.0",
        "endpoints": {
            "download": {
                "path": "/pw",
                "method": "GET",
                "description": "Stream/download video content",
                "parameters": {
                    "url": {
                        "type": "string",
                        "required": True,
                        "description": "Complete video URL (MPD, M3U8, etc.) with optional token"
                    },
                    "token": {
                        "type": "string",
                        "required": False,
                        "description": "JWT token (if not in URL)"
                    }
                },
                "example": "/pw?url=https://cdn.example.com/video.mpd&parentId=123&childId=456&token=eyJ..."
            },
            "health": {
                "path": "/health",
                "method": "GET",
                "description": "API health check"
            }
        },
        "features": [
            "CORS enabled (all origins)",
            "Token-based authentication",
            "Retry logic for transient failures",
            "Range request support",
            "Stream chunking for large files"
        ]
    }), 200


# ═══════════════════════════════════════════════════════════════════════════
# ERROR HANDLERS
# ═══════════════════════════════════════════════════════════════════════════

@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Endpoint not found", "path": request.path}), 404


@app.errorhandler(500)
def server_error(error):
    logger.error(f"Server error: {error}")
    return jsonify({"error": "Internal server error"}), 500


# ═══════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('DEBUG', 'False').lower() == 'true'
    
    logger.info(f"Starting MyProjects MS Video API on port {port}")
    app.run(host='0.0.0.0', port=port, debug=debug)
