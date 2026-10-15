/**
 * Next.js 前端配置：将浏览器同源的 /backend 请求代理至 FastAPI。
 * 定义 nextConfig；通过重写规则转发认证 Cookie 和 SSE 流，不在浏览器暴露后端地址以外的任何凭据。
 */

import type { NextConfig } from "next";

const backendBaseUrl = process.env.BACKEND_API_URL ?? "http://127.0.0.1:8001";

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      {
        source: "/backend/:path*",
        destination: `${backendBaseUrl}/:path*`,
      },
    ];
  },
};

export default nextConfig;
