# Smart Clearance System (Green‑Chain)

A hackathon prototype to reduce food waste by intelligently clearing
near‑expiry inventory using a real‑time, voice‑enabled decision system.

---

## Problem
Warehouses and retailers lose money due to unsold near‑expiry goods.
Manual negotiation with buyers is slow and inefficient.

---

## Solution
Green‑Chain automates clearance decisions using:
- A decision‑making backend
- A real‑time voice assistant
- Automated buyer shortlisting

This enables fast confirmation and export of excess stock.

---

## Architecture Overview
- **Frontend**: HTML + JavaScript (browser UI + voice)
- **Backend**: FastAPI (decision & confirmation logic)
- **Voice Assistant**: Triggered, real‑time, browser‑based (no phone calls)
- **Docker**: Optional setup to run services together

---

## Team Roles
- **Member 1**: Repository management, README, Docker
- **Member 2**: Backend (FastAPI & logic)
- **Member 3**: Frontend UI + browser voice
- **Member 4**: Voice agent flow & documentation

---

## Demo Instructions

### Frontend
1. Open `frontend/index.html` in a browser
2. Click **Start Voice Agent / Run Triage**
3. The system speaks and confirms export decisions

### Backend
Run locally:
```bash
docker-compose up
cd backend
pip install -r requirements.txt
uvicorn app:app --reload
