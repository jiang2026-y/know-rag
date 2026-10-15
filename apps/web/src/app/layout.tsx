/**
 * Web 根布局：加载全局样式并设置页面元数据。
 * 定义 RootLayout；为所有前端页面提供中文语言标记和统一文档结构。
 */

import type { Metadata } from "next";
import type { ReactNode } from "react";

import "./globals.css";

export const metadata: Metadata = {
  title: "LLMcopy 汽车知识问答",
  description: "基于可信身份与受控知识的汽车问答页面",
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
