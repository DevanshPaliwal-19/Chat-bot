# Osiyatech AI Assistant - React Frontend

Modern React + TypeScript + Tailwind CSS frontend for the Osiyatech AI Assistant, replacing the Streamlit interface.

## Features

- **Decoupled Architecture**: Directly connects to the FastAPI backend (`http://localhost:8000`).
- **Interactive Modes**:
  - 💬 **Text Mode**: Real-time chat with document citations, suggested prompts, and action buttons.
  - 🎙️ **Voice Mode**: Browser microphone audio recording using `MediaRecorder` API with waveform animation and automated TTS playback.
  - 🧑‍💼 **Avatar Mode**: Avatar display with animated speaking states when responding with speech.
- **Lead Capture Modal**: Comprehensive project intake form with consent validation and human-handoff options.
- **Low-Confidence Speech Verification**: Allows users to review and edit transcribed audio if STT confidence is low.
- **Session & Conversation Continuity**: Preserves session state and conversation IDs across multiple turns.
- **Mobile Responsive**: Adaptive drawer sidebar and clean mobile-friendly chat UI.

## Getting Started

### 1. Start the FastAPI Backend
In your backend terminal:
```powershell
.\.venv\Scripts\Activate.ps1
.\scripts\start_backend.ps1
# Running on http://127.0.0.1:8000
```

### 2. Start the React Frontend
In a new terminal:
```powershell
cd frontend-react
npm run dev
# or from root: .\scripts\start_frontend_react.ps1
```
Open **`http://localhost:5173`** in Chrome or Edge.

### 3. Production Build
```powershell
npm run build
```
Build output is saved to `frontend-react/dist`.
