Running injest.py
Description: injest.py will create the chroma vector db, and populate the db with the vectors representations for the data sources from the datasources dir. You can also add other pdfs to that
1. Create a powershell terminal
2. Ensure you have python installed in your system, using "py --version" or "python3 --version" or other methods.
3. Run "py -m venv venv" to create a virtual environment
4. If you OS is Windows, try "venv\Scripts\activate" to activate the virtual environment or try other commands for your respective OS.
5. Run "pip install -r requirements_ingest.txt" to recursively install the dependencies for your ingest.py file.
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


2. Activate the venv terminal again\

## Activate it
venv\Scripts\activate

# Install everything pinned

Try this one command

'pip install -r requirements_notebook_compressed.txt'


(or)

Try them one by one


langchain==1.3.4
langchain-community==0.3.24
langchain-core==1.4.1
langchain-groq==1.1.2

langgraph==1.2.4
langgraph-checkpoint==4.1.1
langgraph-prebuilt==1.1.0

chromadb==1.5.9

sentence-transformers==5.5.1
transformers==5.10.2
torch==2.12.0

pypdf==4.3.1
python-dotenv==1.2.2

pandas==3.0.3
numpy==2.4.6

datasets==5.0.0
scikit-learn==1.9.0


pip install pydantic==2.11.4
pip install sentence-transformers==2.7.0
pip install chromadb==0.5.23
pip install groq==0.13.0
pip install reportlab==4.2.5
pip install pypdf==4.3.1
pip install python-dotenv
<!-- pip install langchainhub -->

# Register it as a Jupyter kernel
pip install ipykernel
python -m ipykernel install --user --name tutor_env --display-name "Tutor (clean)"

In `Select Kernel` select Python Environments and Select `venv`

Run the adaptive_tutor_langchain.ipynb file to see how the agents would work


