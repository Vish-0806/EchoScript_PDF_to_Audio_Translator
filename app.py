from flask import Flask, render_template, request

app = Flask(__name__)


@app.route("/")
def index():
	return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload():
	file = request.files.get("pdf_file")
	if file is None:
		return "No file provided", 400
	print(file.filename)
	return "Upload successful"


if __name__ == "__main__":
	app.run(debug=True)
