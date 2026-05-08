# 🎧 EchoScript

EchoScript is a full-stack AI-powered web application that converts PDF documents into natural-sounding audio, with optional multilingual translation before speech generation. It is built as a SaaS-style experience with a clean, futuristic UI and a fast processing pipeline designed for real-world document workloads.

---

## ✨ Features

### 📄 PDF Processing

- Extracts text from uploaded PDF files using `pdfplumber`
- Supports multi-page documents
- Cleans extracted text for better readability and speech quality
- Preserves paragraph structure and removes noisy artifacts such as URLs, citations, and control characters

### 🌍 Translation

- Optional multi-language translation powered by `deep_translator`
- Supports English, Hindi, Spanish, and Kannada in the current build
- Translates content before synthesis when enabled
- Uses chunked translation for large documents to reduce API overhead

### 🔊 Audio Generation

- Neural text-to-speech powered by `edge-tts`
- Natural-sounding voices with speed control
- Chunk-based processing for large documents
- Parallel synthesis to reduce turnaround time
- Temporary file cleanup to keep the pipeline lightweight

### ⚡ Performance

- Multithreaded translation and TTS execution
- Optimized chunking strategy for large PDFs
- Dual pipeline mode for translation + speech generation
- Fast path for non-translated documents

### 🖥️ UI/UX

- Modern, tech-inspired SaaS-style interface
- Fully responsive layout across desktop and mobile
- Smooth motion and polished visual feedback
- Consistent experience across Home, Upload, Review, and Result pages

### 📤 Upload System

- Drag-and-drop PDF upload experience
- Client-side file validation
- Clear upload state and processing feedback

### 🧾 Review Page

- Preview of extracted text before conversion
- Clean formatting for easier inspection
- Confirmation step for language, speed, and translation settings

### 🎧 Result Page

- Custom audio player for playback
- Download option for the generated MP3
- Metadata display for language, voice, speed, translation state, file size, and estimated duration

---

## 🛠️ Tech Stack

### Backend

- Flask
- Python

### Frontend

- HTML
- CSS
- JavaScript
- Bootstrap 5

### Core Libraries

- `pdfplumber` for PDF text extraction
- `deep-translator` for translation
- `edge-tts` for neural text-to-speech generation

---

## 🧠 How It Works

1. The user uploads a PDF through the upload screen.
2. The backend extracts text page by page and calculates basic document metadata.
3. The extracted text is shown on the review page for confirmation.
4. On conversion, EchoScript either:
   - sends the text directly to speech synthesis, or
   - translates it in chunks first, then synthesizes each chunk in parallel.
5. Generated audio chunks are merged into a single MP3 file.
6. The result page provides playback, metadata, and download access.

### Pipeline behavior

- Short or non-translated documents use a fast direct TTS path.
- Large translated documents use an overlapped translation + TTS pipeline.
- Chunk sizes are tuned to balance translation quality, throughput, and stability.

---

## 📊 Performance

| PDF Size | Typical Time |
| --- | --- |
| Small (1–5 pages) | 5–15 sec |
| Medium (6–20 pages) | 15–45 sec |
| Large (20–50 pages) | 45–120 sec |
| Very Large (50+ pages) | 2–5 min |

### Factors that affect processing time

- PDF complexity: scanned layouts, dense formatting, and noisy extraction increase processing cost
- Translation: enabling translation adds external request overhead and chunk processing time
- Network latency: both translation and TTS depend on remote services
- System performance: CPU, memory, and concurrent workload affect threading throughput

---

## 🏗️ Architecture

EchoScript uses a layered pipeline designed for speed and reliability.

### Chunk processing pipeline

- Extract text from PDF pages
- Clean and normalize the extracted content
- Split the text into sentence-aware chunks
- Translate chunks in parallel when required
- Convert chunks to speech concurrently
- Merge the generated audio files in the original order

### Async + multithreading

- `edge-tts` synthesis runs asynchronously per chunk
- `ThreadPoolExecutor` is used to parallelize translation and synthesis
- The pipeline keeps workers busy by overlapping translation and audio generation

### Audio merging

- Each chunk is written to a temporary MP3 file
- Files are validated before merging
- The final output is assembled into `static/output.mp3`
- Temporary files are removed after completion
---

## 🚀 Getting Started

### 1) Clone the repository

```bash
git clone https://github.com/Vish-0806/EchoScript_PDF_to_Audio_Translator.git
cd EchoScript_PDF_to_Audio_Translator
```

### 2) Install dependencies

```bash
pip install -r requirements.txt
```

### 3) Run the application

```bash
python app.py
```

### 4) Open the app in your browser

```bash
http://127.0.0.1:5000
```

### Requirements

- Python 3.10+ recommended
- Internet connection required for translation and TTS services
- A valid PDF file for conversion

---

## 💡 Use Cases

- Students turning lecture notes and research papers into listenable audio
- Professionals reviewing reports, proposals, and documentation on the go
- Accessibility-focused reading experiences for users who prefer audio over text
- Multilingual content consumption for global teams and audiences
- Fast document review while commuting, exercising, or multitasking

---

## 🔭 Future Enhancements

- Add more languages and voice options
- Support page-range selection before conversion
- Add user accounts and conversion history
- Store generated outputs in cloud storage
- Improve progress tracking with live stage indicators
- Add OCR support for scanned PDFs
- Introduce queue-based background jobs for very large files

---

## 📈 Project Status

- Status: Active and functional
- Core PDF-to-audio workflow: Implemented
- Translation support: Implemented for selected languages
- UI pages: Implemented and responsive
- Production readiness: Strong prototype / portfolio-grade foundation with room for scaling

---

## 👤 Author

- Vishal

---

## LiveOn

- https://echoscript-pdf-to-audio-translator.onrender.com

---

## 📝 Final Note

EchoScript is built to feel like a polished product, not a demo. It combines document extraction, translation, and neural voice synthesis into a focused workflow that is useful, presentable, and easy to extend.

If you are exploring the project as a recruiter, reviewer, or developer, the main strengths are its structured pipeline, responsive interface, and practical performance strategy for real PDF workloads.
