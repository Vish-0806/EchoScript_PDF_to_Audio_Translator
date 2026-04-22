from flask import Flask, render_template, request, redirect, url_for, jsonify
from io import BytesIO
import pdfplumber
import edge_tts
import asyncio
import re
import os
import tempfile
import threading
import time
import queue
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor, as_completed
from deep_translator import GoogleTranslator

app = Flask(__name__)
CHUNK_SIZE = 3000  # Translation uses larger chunks to reduce API calls.
TTS_CHUNK_SIZE = 2800  # TTS uses smaller chunks to increase parallelism.
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

# Store the most recent UI config values for display fallback.
last_config = {"translate": True}

# Thread pool executor for background tasks
executor = ThreadPoolExecutor(max_workers=5)


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
	"""Split text into sentence-aware chunks for parallel processing."""
	# Sentence-aware boundaries improve translation coherence and more natural TTS phrasing.
	# if not text:
	# 	print("[CHUNK DEBUG] original text length=0, chunks=0, total chunk length=0")
	# 	return []

	# Preserve separators so reconstructed chunks keep full original content.
	sentences = []
	start = 0
	for match in re.finditer(r'(?<=[.!?]) +', text):
		end = match.end()
		sentences.append(text[start:end])
		start = end
	if start < len(text):
		sentences.append(text[start:])
	if not sentences:
		sentences = [text]

	chunks = []
	current_chunk = ""

	for sentence in sentences:
		# Handle very long sentences safely without dropping data.
		if len(sentence) > chunk_size:
			if current_chunk:
				chunks.append(current_chunk)
				current_chunk = ""
			for i in range(0, len(sentence), chunk_size):
				piece = sentence[i:i + chunk_size]
				if piece:
					chunks.append(piece)
			continue

		if not current_chunk:
			current_chunk = sentence
		elif len(current_chunk) + len(sentence) <= chunk_size:
			current_chunk += sentence
		else:
			chunks.append(current_chunk)
			current_chunk = sentence

	if current_chunk:
		chunks.append(current_chunk)

	total_length = sum(len(chunk) for chunk in chunks)
	print(f"[CHUNK DEBUG] original text length={len(text)}, chunks={len(chunks)}, total chunk length={total_length}")
	if total_length < len(text):
		raise ValueError("Text loss detected during chunking")

	return chunks


def translate_text_parallel(text, target_language, chunk_size=CHUNK_SIZE, max_workers=4):
	"""Translate text with smart chunking: larger chunks reduce translation API calls."""
	# For short text, skip chunking overhead and use single translator call
	if len(text) < chunk_size:
		translator = GoogleTranslator(source="auto", target=target_language)
		return translator.translate(text)

	# For large text, use parallel chunk translation
	chunks = split_into_chunks(text, chunk_size=chunk_size)
	if not chunks:
		return ""

	translated_chunks = [""] * len(chunks)
	# Create a translator per worker for thread safety.
	def _translate_chunk(index, chunk_text):
		translator = GoogleTranslator(source="auto", target=target_language)
		return index, translator.translate(chunk_text)

	with ThreadPoolExecutor(max_workers=max_workers) as translation_executor:
		futures = {
			translation_executor.submit(_translate_chunk, index, chunk): index
			for index, chunk in enumerate(chunks)
		}

		for future in as_completed(futures):
			index = futures[future]
			try:
				result_index, translated_text = future.result()
				translated_chunks[result_index] = translated_text or ""
			except Exception as e:
				raise RuntimeError(f"Translation chunk {index} failed: {str(e)}")

	# Merge chunks in original order, ensuring no text loss
	return "".join(translated_chunks)


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


def generate_audio(text, conversion_id, voice, rate_multiplier, text_is_clean=False):
	"""Generate audio from text using optimized parallel chunk processing."""
	global audio_status
	start_time = time.time()
	print("[SLOW MODE ACTIVE] Starting background audio generation with translation...")
	try:
		# Update status: processing started
		audio_status = {"processing": True, "ready": False, "progress": 0}
		
		cleaned_text = text if text_is_clean else clean_text(text)
		if not cleaned_text:
			raise ValueError("No text to convert after cleaning")
		
		# Prepare text for speech once (not per chunk)
		prepared_text = prepare_text_for_speech(cleaned_text)
		
		# TTS uses smaller chunks to maximize parallel synthesis throughput.
		chunks = split_into_chunks(prepared_text, chunk_size=TTS_CHUNK_SIZE)
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
			# Generate all chunks in parallel with higher worker count for faster synthesis
			with ThreadPoolExecutor(max_workers=6) as chunk_executor:
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
		
		elapsed_time = time.time() - start_time
		print(f"[SLOW MODE] ✓ Audio generation + translation completed in {elapsed_time:.2f}s")
		
	except Exception as e:
		# Update status on error
		audio_status = {"processing": False, "ready": False, "progress": 0, "error": str(e)}
		pending_conversions.pop(conversion_id, None)


def generate_audio_fast(text, voice, rate_multiplier, text_is_clean=False, use_chunked=None):
	"""Generate FAST mode audio synchronously with adaptive chunk strategy."""
	global audio_status
	start_time = time.time()
	print("[FAST MODE ACTIVE] Starting synchronous audio generation...")
	try:
		cleaned_text = text if text_is_clean else clean_text(text)
		if not cleaned_text:
			print("[FAST MODE] Error: No text after cleaning")
			return False
		
		# 🚀 FIX: avoid re-processing if already cleaned & prepared
		if text_is_clean:
			prepared_text = text
		else:
			prepared_text = prepare_text_for_speech(cleaned_text)
		if not prepared_text:
			print("[FAST MODE] Error: No text after speech preparation")
			return False

		os.makedirs(app.static_folder, exist_ok=True)
		output_path = os.path.join(app.static_folder, "output.mp3")

		# Use chunked parallel synthesis for large texts; single call for short text.
		# If use_chunked is provided, honor explicit mode selection from /convert.
		should_use_chunked = use_chunked if use_chunked is not None else len(prepared_text) > 3000
		if should_use_chunked:
			print("[FAST MODE] Large text detected → switching to chunked fast mode")
			# TTS uses smaller chunks to maximize parallel synthesis throughput.
			chunks = split_into_chunks(prepared_text, chunk_size=TTS_CHUNK_SIZE)
			temp_chunk_paths = []

			for index in range(len(chunks)):
				temp_path = os.path.join(
					tempfile.gettempdir(),
					f"echoscript_fast_{uuid4().hex}_{index:04d}.mp3"
				)
				temp_chunk_paths.append(temp_path)

			try:
				with ThreadPoolExecutor(max_workers=4) as chunk_executor:
					futures = {
						chunk_executor.submit(synthesize_chunk, chunk, chunk_path, voice, rate_multiplier): index
						for index, (chunk, chunk_path) in enumerate(zip(chunks, temp_chunk_paths))
					}

					for future in as_completed(futures):
						chunk_index = futures[future]
						try:
							future.result()
						except Exception as e:
							raise RuntimeError(f"FAST chunk {chunk_index} synthesis failed: {str(e)}")

				for index, chunk_path in enumerate(temp_chunk_paths):
					if not os.path.exists(chunk_path):
						raise RuntimeError(f"FAST chunk {index} file missing: {chunk_path}")

				with open(output_path, "wb") as output_file:
					for chunk_path in temp_chunk_paths:
						with open(chunk_path, "rb") as chunk_file:
							output_file.write(chunk_file.read())
			finally:
				for chunk_path in temp_chunk_paths:
					try:
						if os.path.exists(chunk_path):
							os.remove(chunk_path)
					except Exception:
						pass
		else:
			synthesize_chunk(prepared_text, output_path, voice, rate_multiplier)

		audio_status = {"processing": False, "ready": True, "progress": 100}
		elapsed_time = time.time() - start_time
		print(f"[FAST MODE] ✓ Audio generation completed in {elapsed_time:.2f}s")
		return True

	except Exception as e:
		audio_status = {"processing": False, "ready": False, "progress": 0, "error": str(e)}
		print(f"[FAST MODE] Error: {e}")
		return False


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
	translate_enabled = request.form.get("translate") == "on"
	voice = "en-US-AriaNeural"
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

	needs_translation = translate_enabled and language != "en"
	if not needs_translation:
		language = "en"
		voice = "en-US-AriaNeural"

	last_config["translate"] = translate_enabled
	
	print("\n" + "="*60)
	print("[/convert] Processing request")
	print(f"  Translation: {'ENABLED' if translate_enabled else 'DISABLED'}")
	print(f"  Target Language: {language}")
	print(f"  Speed: {speed} ({rate_multiplier}x)")
	print("="*60)
	
	# AUTO MODE 1: no translation needed (translate OFF OR language is English)
	# If short text (<3000): direct TTS; else chunked parallel FAST mode.
	if not needs_translation:
		use_chunked_fast = len(text) >= 3000
		if use_chunked_fast:
			print("[/convert] → FAST CHUNKED MODE (no translation, large text)\n")
		else:
			print("[/convert] → FAST DIRECT MODE (no translation, short text)\n")

		success = generate_audio_fast(
			text,
			voice,
			rate_multiplier,
			text_is_clean=False,
			use_chunked=use_chunked_fast,
		)
		
		if not success:
			return "Audio generation failed", 500
		
		# Redirect directly to audio page (instant feedback, no polling)
		return redirect(url_for("audio_ready", estimated_minutes=estimated_minutes, language=language, voice=voice, speed=speed, translate=translate_enabled))

	# AUTO MODE 2: translation + TTS pipeline per chunk for maximum overlap.
	print("[/convert] → TRANSLATION + TTS PIPELINE MODE\n")
	voice = LANGUAGE_VOICE_MAP.get(language, "en-US-AriaNeural")
	chunks = split_into_chunks(text, chunk_size=CHUNK_SIZE)
	if not chunks:
		return "No text available for conversion", 400

	audio_status = {"processing": True, "ready": False, "progress": 0}
	temp_chunk_paths = []
	for index in range(len(chunks)):
		temp_path = os.path.join(
			tempfile.gettempdir(),
			f"echoscript_pipeline_{uuid4().hex}_{index:04d}.mp3"
		)
		temp_chunk_paths.append(temp_path)

	try:
		# Dual-pipeline design overlaps translation and TTS so workers stay busy.
		# Separating stages reduces idle time versus per-chunk sequential processing.
		prepared_queue = queue.Queue()
		translation_errors = []
		tts_errors = []
		progress_lock = threading.Lock()
		completed_chunks = 0
		translation_worker_count = 5
		tts_worker_count = 5

		def translate_worker(index, chunk_text):
			translated_text = GoogleTranslator(
				source="auto",
				target=language
			).translate(chunk_text) if chunk_text.strip() else ""

			cleaned_text = clean_text(translated_text)
			prepared_text = prepare_text_for_speech(cleaned_text if cleaned_text else translated_text)
			if not prepared_text.strip():
				prepared_text = "."
			prepared_queue.put((index, prepared_text))
			if index % 10 == 0:
				print(f"[PIPELINE] Queue size: {prepared_queue.qsize()}")
			return index

		def tts_worker():
			nonlocal completed_chunks
			while True:
				item = prepared_queue.get()
				try:
					if item is None:
						return
					index, prepared_text = item
					synthesize_chunk(prepared_text, temp_chunk_paths[index], voice, rate_multiplier)
					with progress_lock:
						completed_chunks += 1
						audio_status["progress"] = int((completed_chunks / len(chunks)) * 100)
						if completed_chunks % 10 == 0:
							print(f"[PIPELINE] Queue size: {prepared_queue.qsize()}")
				except Exception as e:
					tts_errors.append(str(e))
				finally:
					prepared_queue.task_done()

		with ThreadPoolExecutor(max_workers=tts_worker_count) as tts_executor:
			tts_futures = [tts_executor.submit(tts_worker) for _ in range(tts_worker_count)]

			with ThreadPoolExecutor(max_workers=translation_worker_count) as translation_executor:
				translation_futures = {
					translation_executor.submit(translate_worker, index, chunk): index
					for index, chunk in enumerate(chunks)
				}

				for future in as_completed(translation_futures):
					chunk_index = translation_futures[future]
					try:
						future.result()
					except Exception as e:
						translation_errors.append(f"Translation chunk {chunk_index} failed: {str(e)}")

			# Signal TTS workers that translation is finished.
			for _ in range(tts_worker_count):
				prepared_queue.put(None)

			# Wait until queue is fully processed before merge.
			prepared_queue.join()

			for future in tts_futures:
				future.result()

		if translation_errors:
			raise RuntimeError(translation_errors[0])
		if tts_errors:
			raise RuntimeError(f"TTS worker failed: {tts_errors[0]}")

		for index, chunk_path in enumerate(temp_chunk_paths):
			if not os.path.exists(chunk_path):
				raise RuntimeError(f"Pipeline chunk {index} file missing: {chunk_path}")

		os.makedirs(app.static_folder, exist_ok=True)
		output_path = os.path.join(app.static_folder, "output.mp3")

		with open(output_path, "wb") as output_file:
			for chunk_path in temp_chunk_paths:
				with open(chunk_path, "rb") as chunk_file:
					output_file.write(chunk_file.read())

		audio_status = {"processing": False, "ready": True, "progress": 100}
	except Exception as e:
		audio_status = {"processing": False, "ready": False, "progress": 0, "error": str(e)}
		return "Audio generation failed", 500
	finally:
		for chunk_path in temp_chunk_paths:
			try:
				if os.path.exists(chunk_path):
					os.remove(chunk_path)
			except Exception:
				pass

	return redirect(url_for("audio_ready", estimated_minutes=estimated_minutes, language=language, voice=voice, speed=speed, translate=translate_enabled))


@app.route("/status")
def status():
	"""Return current audio generation status as JSON."""
	return jsonify(audio_status)


@app.route("/audio_ready")
def audio_ready():
	estimated_minutes = request.args.get("estimated_minutes", type=int)
	language = request.args.get("language", "en")
	voice = request.args.get("voice", LANGUAGE_VOICE_MAP.get(language, "en-US-AriaNeural"))
	speed = request.args.get("speed", "normal")
	translate_raw = request.args.get("translate")
	if translate_raw is None:
		translate_enabled = last_config.get("translate", True)
	else:
		translate_enabled = translate_raw.lower() == "true"
	output_path = os.path.join(app.static_folder, "output.mp3")
	file_size = None

	if os.path.exists(output_path):
		size_bytes = os.path.getsize(output_path)
		file_size = round(size_bytes / 1024 / 1024, 2)

	return render_template(
		"audio.html",
		estimated_minutes=estimated_minutes,
		file_size=file_size,
		language=language,
		voice=voice,
		speed=speed,
		translate=translate_enabled,
	)


if __name__ == "__main__":
	app.run(debug=True, use_reloader = False , threaded=True)