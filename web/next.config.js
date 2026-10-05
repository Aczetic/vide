/** @type {import('next').NextConfig} */
module.exports = {
  async rewrites() {
    // The API runs separately in development; proxying keeps the browser on one
    // origin so there is no CORS dance and no API URL baked into the client.
    return [{ source: '/api/:path*', destination: 'http://127.0.0.1:8000/api/:path*' }]
  },
}
