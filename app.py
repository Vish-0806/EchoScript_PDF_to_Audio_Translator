from flask import Flask, render_template, request, redirect, url_for, jsonify
from io import BytesIO
import pdfplumber
import edge_tts
import asyncio
import re
import os
import tempfile
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor, as_completed
from deep_translator import GoogleTranslator

app = Flask(__name__)
CHUNK_SIZE = 4000  # Optimal size for Edge TTS network efficiency
LANGUAGE_VOICE_MAP = {
	"en": "en-US-AriaNeural",
	"hi": "hi-IN-SwaraNeural",
	"kn": "kn-IN-SapnaNeural",
	"es": "es-ES-ElviraNeural",
}

# Temporary storage for extracted text (cleared after conversion)
pending_conversions = {}

# Audio generation status
audio_status = {"processing": False, "ready": False, "progress": 0}

# Thread pool executor for background tasks
executor = ThreadPoolExecutor(max_workers=1)


def clean_text(text):
	"""Aggressively clean and normalize text for clean speech synthesis."""
	# Remove all URLs including any standalone URLs
	cleaned = re.sub(r"\b\S*https?\S*\b", "", text, flags=re.IGNORECASE)
	cleaned = re.sub(r"https?://\S+", "", cleaned)
	cleaned = re.sub(r"www\.\S+", "", cleaned)
	
	# Remove any remaining http/https strings
	cleaned = re.sub(r"https?", "", cleaned, flags=re.IGNORECASE)
	
	# Remove bracketed references/citations like [1], [a], [1][2], [citation needed], [note 1]
	cleaned = re.sub(r"\[[^\]]*\]", "", cleaned)
	
	# Remove any leftover unmatched square brackets
	cleaned = cleaned.replace("[", "").replace("]", "")
	
	# Remove HTML tags and SSML fragments
	cleaned = re.sub(r"<[^>]+>", "", cleaned)
	
	# Remove broken unicode characters and control characters
	cleaned = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", cleaned)
	cleaned = re.sub(r"\ufffd", "", cleaned)
	
	# Remove excessive dots like "... ..."
	cleaned = re.sub(r"\.{3,}", ".", cleaned)
	
	# Fix hyphenated line breaks
	cleaned = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", cleaned)
	
	# Preserve paragraph structure with double newlines
	cleaned = re.sub(r"\n\s*\n+", "\n\n", cleaned)
	
	# Normalize whitespace within lines but preserve sentence flow
	cleaned = re.sub(r"[ \t]+", " ", cleaned)
	cleaned = re.sub(r" \n", "\n", cleaned)
	cleaned = re.sub(r"\n ", "\n", cleaned)
	
	# Clean spacing introduced near punctuation by removed references
	cleaned = re.sub(r"\s+([,.;:!?])", r"\1", cleaned)
	
	# Re-normalize whitespace after all bracket/reference removals
	cleaned = re.sub(r"[ \t]+", " ", cleaned)
	cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
	cleaned = re.sub(r" \n", "\n", cleaned)
	cleaned = re.sub(r"\n ", "\n", cleaned)
	
	# Remove special characters that might cause issues
	cleaned = re.sub(r"[<>{}]", "", cleaned)
	
	return cleaned.strip()


def prepare_text_for_speech(text):
	"""Prepare text for natural speech synthesis."""
	# Replace paragraph breaks with period and space for natural pauses
	prepared = text.replace("\n\n", ". ")
	
	# Replace single line breaks with spaces
	prepared = prepared.replace("\n", " ")
	
	# Normalize multiple spaces
	prepared = re.sub(r"  +", " ", prepared)
	
	# Fix excessive periods (but keep sentence endings)
	prepared = re.sub(r"\.{2,}", ".", prepared)
	
	# Clean up spacing around periods
	prepared = re.sub(r"\s*\.\s*", ". ", prepared)
	prepared = re.sub(r"\.\s+\.", ".", prepared)
	
	return prepared.strip()


def split_into_chunks(text, chunk_size=CHUNK_SIZE):
	"""Split text into chunks for parallel processing."""
	return [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]


def synthesize_chunk(chunk_text, chunk_path, voice, rate_multiplier):
	"""Synthesize a single chunk using Edge TTS with plain text."""
	async def _synthesize():
		if rate_multiplier == 1.0:
			communicate = edge_tts.Communicate(text=chunk_text, voice=voice)
		else:
			rate_value = f"{int((rate_multiplier - 1.0) * 100):+d}%"
			communicate = edge_tts.Communicate(text=chunk_text, voice=voice, rate=rate_value)
		await communicate.save(chunk_path)
	
	loop = asyncio.new_event_loop()
	try:
		asyncio.set_event_loop(loop)
		loop.run_until_complete(_synthesize())
	finally:
		asyncio.set_event_loop(None)
		loop.close()


def generate_audio(text, conversion_id, voice, rate_multiplier):
	"""Generate audio from text using optimized parallel chunk processing."""
	global audio_status
	try:
		# Update status: processing started
		audio_status = {"processing": True, "ready": False, "progress": 0}
		
		cleaned_text = clean_text(text)
		if not cleaned_text:
			raise ValueError("No text to convert after cleaning")
		
		# Prepare text for speech once (not per chunk)
		prepared_text = prepare_text_for_speech(cleaned_text)
		
		# Split already-prepared text into chunks
		chunks = split_into_chunks(prepared_text)
		total_chunks = len(chunks)
		completed_chunks = 0
		
		# Create temporary chunk files with indexed names
		temp_chunk_paths = []
		for index in range(len(chunks)):
			temp_path = os.path.join(
				tempfile.gettempdir(),
				f"echoscript_{uuid4().hex}_{index:04d}.mp3"
			)
			temp_chunk_paths.append(temp_path)
		
		try:
			# Generate all chunks in parallel with 4 workers (optimal for Edge TTS)
			with ThreadPoolExecutor(max_workers=4) as chunk_executor:
				# Submit all chunks
				futures = {
					chunk_executor.submit(synthesize_chunk, chunk, chunk_path, voice, rate_multiplier): index
					for index, (chunk, chunk_path) in enumerate(zip(chunks, temp_chunk_paths))
				}
				
				# Wait for all chunks to complete with error handling
				for future in as_completed(futures):
					chunk_index = futures[future]
					try:
						future.result()
						completed_chunks += 1
						# Update progress percentage
						audio_status["progress"] = int((completed_chunks / total_chunks) * 100)
					except Exception as e:
						raise RuntimeError(f"Chunk {chunk_index} synthesis failed: {str(e)}")
			
			# Verify all chunk files exist before merging
			for index, chunk_path in enumerate(temp_chunk_paths):
				if not os.path.exists(chunk_path):
					raise RuntimeError(f"Chunk {index} file missing: {chunk_path}")
			
			# Merge chunks in exact original order
			os.makedirs(app.static_folder, exist_ok=True)
			output_path = os.path.join(app.static_folder, "output.mp3")
			
			with open(output_path, "wb") as output_file:
				for chunk_path in temp_chunk_paths:
					with open(chunk_path, "rb") as chunk_file:
						output_file.write(chunk_file.read())
		
		finally:
			# Always clean up temporary files
			for chunk_path in temp_chunk_paths:
				try:
					if os.path.exists(chunk_path):
						os.remove(chunk_path)
				except Exception:
					pass  # Ignore cleanup errors
		
		# Update status: conversion completed successfully
		audio_status = {"processing": False, "ready": True, "progress": 100}
		
		# Clean up temporary storage
		pending_conversions.pop(conversion_id, None)
		
	except Exception as e:
		# Update status on error
		audio_status = {"processing": False, "ready": False, "progress": 0, "error": str(e)}
		pending_conversions.pop(conversion_id, None)


@app.route("/")
def index():
	return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload():
	file = request.files.get("pdf_file")
	if not file:
		return "No file provided", 400
	
	# Extract text from PDF
	file_stream = BytesIO(file.read())
	full_text = ""
	page_count = 0
	
	with pdfplumber.open(file_stream) as pdf:
		page_count = len(pdf.pages)
		for page in pdf.pages:
			full_text += page.extract_text() or ""
	
	if not full_text.strip():
		return "No text found in PDF", 400

	word_count = len(full_text.split())
	estimated_minutes = int(round(word_count / 160))
	
	# Store for conversion
	conversion_id = uuid4().hex
	pending_conversions[conversion_id] = {
		"text": full_text,
		"estimated_minutes": estimated_minutes,
	}
	
	return render_template(
		"result.html",
		conversion_id=conversion_id,
		text=full_text,
		page_count=page_count,
		word_count=word_count,
		estimated_minutes=estimated_minutes,
	)


@app.route("/convert", methods=["POST"])
def convert():
	global audio_status
	conversion_id = request.form.get("conversion_id", "")
	speed = request.form.get("speed", "normal")
	language = request.form.get("language", "en")
	voice = LANGUAGE_VOICE_MAP.get(language, "en-US-AriaNeural")
	rate_multiplier_map = {
		"slow": 0.8,
		"normal": 1.0,
		"fast": 1.2
	}
	rate_multiplier = rate_multiplier_map.get(speed, 1.0)
	conversion_data = pending_conversions.get(conversion_id)
	
	if not conversion_data:
		return "No text available for conversion", 400

	text = conversion_data.get("text", "")
	estimated_minutes = conversion_data.get("estimated_minutes")

	if not text:
		return "No text available for conversion", 400

	if language != "en":
		text = GoogleTranslator(source="auto", target=language).translate(text)
	
	# Start audio generation in background thread
	executor.submit(generate_audio, text, conversion_id, voice, rate_multiplier)
	
	# Redirect to processing page immediately (non-blocking)
	return render_template(
		"processing.html",
		conversion_id=conversion_id,
		estimated_minutes=estimated_minutes,
	)


@app.route("/status")
def status():
	"""Return current audio generation status as JSON."""
	return jsonify(audio_status)


@app.route("/audio_ready")
def audio_ready():
	estimated_minutes = request.args.get("estimated_minutes", type=int)
	output_path = os.path.join(app.static_folder, "output.mp3")
	file_size = None

	if os.path.exists(output_path):
		size_bytes = os.path.getsize(output_path)
		file_size = round(size_bytes / 1024 / 1024, 2)

	return render_template(
		"audio.html",
		estimated_minutes=estimated_minutes,
		file_size=file_size,
	)


if __name__ == "__main__":
	app.run(debug=True)