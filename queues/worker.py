from dotenv import load_dotenv
from openai import OpenAI
from langchain_ollama import OllamaEmbeddings
from langchain_qdrant import QdrantVectorStore

load_dotenv()

local_client = OpenAI(
    api_key = "ollama",
    base_url="http://localhost:11434/v1/"
)

# Vector Embeddings
embedding_model = OllamaEmbeddings(
    model="embeddinggemma",
    base_url="http://localhost:11434",
)

vector_db = QdrantVectorStore.from_existing_collection(
    url="http://localhost:6333",
    collection_name="learning_rag_local",
    embedding=embedding_model,
)


async def process_query(query:str):
    print("Searching Chunks", query)
    search_results = vector_db.similarity_search(query=query)

    context = "\n\n\n".join([f"Page Content: {result.page_content}\nPage Number: {result.metadata['page_label']}\nFile Location: {result.metadata['source']}" for result in search_results])

    SYSTEM_PROMPT = f"""
    You are a helpfull AI Assistant who answeres user query based on the available context retrieved from a PDF file along with page_contents and page number.

    You should only ans the user based on the following context and navigate the user to open the right page number to know more.

    Context:
    {context}
    """

    response = local_client.chat.completions.create(
        model="gemma:2b",
        messages=[
            { "role": "system", "content":SYSTEM_PROMPT  },
            { "role": "user", "content":query  },
        ]
    )

    print(f"🤖: {response.choices[0].message.content}")
    return response.choices[0].message.content


