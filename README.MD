Running injest.py
Description: injest.py will create the chroma vector db, and populate the db with the vectors representations for the data sources from the datasources dir. You can also add other pdfs to that
1. Create a powershell terminal
2. Ensure you have python installed in your system, using "py --version" or "python3 --version" or other methods.
3. Run "py -m venv venv" to create a virtual environment
4. If you OS is Windows, try "venv\Scripts\activate" to activate the virtual environment or try other commands for your respective OS.
5. Run "pip install -r requirements" to recursively install the dependencies for your ingest.py file.
6. Run "py .\ingest.py" and you should see an output like:


Found 1 PDF files
Warning: You are sending unauthenticated requests to the HF Hub. Please set a HF_TOKEN to enable higher rate limits and faster downloads.
Loading weights: 100%|████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████| 103/103 [00:00<00:00, 3357.45it/s]

Processing: leac203.pdf
Extracted text length: 47339 characters
Total chunks created: 60
Generating embeddings...
Adding chunks to ChromaDB...
leac203.pdf stored successfully.

All PDFs processed successfully.


2.