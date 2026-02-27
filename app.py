from flask import Flask, render_template, request
from io import BytesIO
import pdfplumber

app = Flask(__name__)

@app.route("/convert", methods=["POST"])
def convert():
    text = request.form.get("text_data")

    print("Convert button clicked")

    return "Audio conversion will be implemented next!"


@app.route("/")
def index():
	return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload():
	file = request.files.get("pdf_file")

	if file is None:
		return "No file provided", 400
	
	# Read file from memory
	file_stream = BytesIO(file.read())
	
	# Extract text from PDF
	extracted_text = ""
	with pdfplumber.open(file_stream) as pdf:
		for page in pdf.pages:
			extracted_text += page.extract_text() or ""
	
	return render_template("result.html", text=extracted_text)


if __name__ == "__main__":
    app.run(debug=True)