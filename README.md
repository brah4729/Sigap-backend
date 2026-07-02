---
title: SIGAP Backend
emoji: 🚨
colorFrom: red
colorTo: gray
sdk: docker
pinned: false
---

# SIGAP Backend API

FastAPI backend for SIGAP — AI-powered disaster response coordination for Indonesia.

**API Docs:** `/docs`  
**Health Check:** `/`

## Setup

Set these environment variables in your Space settings:

```
GOOGLE_API_KEY=your_key
GOOGLE_API_KEY_MONITOR=your_key
GOOGLE_API_KEY_ASSESSMENT=your_key
GOOGLE_API_KEY_COORDINATOR=your_key
GOOGLE_API_KEY_ORCHESTRATOR=your_key
GEMINI_MODEL=gemini-2.5-flash-lite
JWT_SECRET=your_random_secret
JWT_ALGORITHM=HS256
DATABASE_URL=sqlite+aiosqlite:///./sigap.db
APP_ENV=production
```
