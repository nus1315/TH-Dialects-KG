import os
import sys
import json
import time
import numpy as np
import pandas as pd
import faiss
from sentence_transformers import SentenceTransformer
from neo4j import GraphDatabase
import re

# Paths
CSV_PATH = "Data/พจนานุกรมภาษาไทยถิ่น 4 ภาค (cleaned data without line breaks).xlsx - Sheet1.csv"
INDEX_PATH = "Data/meanings.index"
METADATA_PATH = "Data/meanings_metadata.json"

# Neo4j Details
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_AUTH = ("neo4j", "password123")

def clean_word(w):
    if not w:
        return ""
    w = str(w).replace('\xa0', ' ')
    w = re.sub(r'\s+', ' ', w)
    return w.strip()

def parse_dialect_column(cell):
    if pd.isna(cell):
        return []
    parts = str(cell).split('/')
    words = []
    for p in parts:
        w = clean_word(p)
        if w:
            words.append(w)
    return words

def build_index(limit=None):
    print(f"Reading CSV from {CSV_PATH}...")
    try:
        df = pd.read_csv(CSV_PATH)
    except Exception as e:
        print(f"Error reading CSV: {e}")
        return

    if limit is not None:
        df = df.head(limit)
        print(f"Limiting build index to the first {limit} rows for quick compiling.")

    print("Loading BGE-M3 model (BAAI/bge-m3)...")
    # This will download the model to HF cache inside our large workspace
    model = SentenceTransformer("BAAI/bge-m3")
    
    meanings = []
    metadata = []
    
    print("Preprocessing dictionary entries...")
    for index, row in df.iterrows():
        try:
            seq_str = str(row['ลำดับ']).strip()
            if seq_str.startswith('า'):
                seq_str = '1' + seq_str[1:]
            entry_id = int(seq_str)
        except Exception:
            entry_id = index + 1
            
        pos = clean_word(row['ชนิดของคำ']) if not pd.isna(row['ชนิดของคำ']) else ""
        meaning = clean_word(row['ความหมาย']) if not pd.isna(row['ความหมาย']) else ""
        
        if not meaning:
            continue
            
        central = parse_dialect_column(row['กลาง'])
        southern = parse_dialect_column(row['ใต้'])
        northern = parse_dialect_column(row['เหนือ'])
        northeastern = parse_dialect_column(row['อีสาน'])
        
        # Determine the hub word
        hub_word = ""
        if central:
            hub_word = central[0]
        elif southern:
            hub_word = southern[0]
        elif northern:
            hub_word = northern[0]
        elif northeastern:
            hub_word = northeastern[0]
            
        meanings.append(meaning)
        metadata.append({
            "entry_id": entry_id,
            "meaning": meaning,
            "pos": pos,
            "hub_word": hub_word,
            "words": {
                "กลาง": central,
                "ใต้": southern,
                "เหนือ": northern,
                "อีสาน": northeastern
            }
        })
        
    print(f"Encoding {len(meanings)} definitions (this might take a minute)...")
    start_time = time.time()
    # Embed meanings using BGE-M3
    embeddings = model.encode(meanings, show_progress_bar=True, convert_to_numpy=True)
    print(f"Encoding completed in {time.time() - start_time:.2f} seconds!")
    
    # Normalize embeddings for Cosine Similarity (Inner Product)
    faiss.normalize_L2(embeddings)
    
    # Build FAISS Index
    dimension = embeddings.shape[1]
    print(f"Building FAISS Index (Dimension: {dimension})...")
    index = faiss.IndexFlatIP(dimension)
    index.add(embeddings)
    
    # Save index and metadata
    os.makedirs("Data", exist_ok=True)
    faiss.write_index(index, INDEX_PATH)
    with open(METADATA_PATH, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
        
    print(f"Successfully built and saved FAISS Index to {INDEX_PATH}")
    print(f"Saved metadata to {METADATA_PATH}")

def search(query_text, top_k=5):
    if not os.path.exists(INDEX_PATH) or not os.path.exists(METADATA_PATH):
        print("Error: FAISS Index or Metadata not found! Please run with '--build' first.")
        print("Example: python3 src/semantic_search.py --build")
        return
        
    print(f"Loading FAISS Index from {INDEX_PATH}...")
    index = faiss.read_index(INDEX_PATH)
    
    print(f"Loading Metadata from {METADATA_PATH}...")
    with open(METADATA_PATH, "r", encoding="utf-8") as f:
        metadata = json.load(f)
        
    print("Loading BGE-M3 model...")
    model = SentenceTransformer("BAAI/bge-m3")
    
    print(f"\nEncoding search query: '{query_text}'...")
    query_vector = model.encode([query_text], convert_to_numpy=True)
    faiss.normalize_L2(query_vector)
    
    # Search
    distances, indices = index.search(query_vector, top_k)
    
    print("\n" + "="*80)
    print(f"🔍 SEMANTIC SEARCH RESULTS (Top {top_k} matches for '{query_text}'):")
    print("="*80)
    
    # Initialize Neo4j driver to retrieve rich synonym graph
    neo4j_driver = None
    try:
        neo4j_driver = GraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)
        neo4j_driver.verify_connectivity()
    except Exception:
        print("⚠️ Warning: Could not connect to Neo4j. Skipping rich graph synonym retrieval.")
        
    for idx, (dist, meta_idx) in enumerate(zip(distances[0], indices[0])):
        meta = metadata[meta_idx]
        print(f"\n[Rank {idx+1}] (Similarity Score: {dist:.4f})")
        print(f"📖 Meaning: {meta['meaning']} ({meta['pos']})")
        print(f"🔑 Central Reference Word (Hub): '{meta['hub_word']}'")
        
        # Display list of words from FAISS Metadata
        words_str = []
        for dialect, w_list in meta['words'].items():
            if w_list:
                words_str.append(f"{dialect}: {' / '.join(w_list)}")
        print(f"🌍 Words: {', '.join(words_str)}")
        
        # Query Neo4j to find real graph connections
        if neo4j_driver:
            with neo4j_driver.session() as session:
                query = """
                MATCH (w:Word)-[r:EXPRESSES]->(hub:Word)
                WHERE r.entry_id = $entry_id
                RETURN w.text AS word, r.dialect AS dialect
                """
                results = session.run(query, entry_id=meta['entry_id']).data()
                if results:
                    synonyms = [f"{r['word']} ({r['dialect']})" for r in results]
                    print(f"🔗 Neo4j Graph Synonyms: {' ↔ '.join(synonyms)}")
                    
    if neo4j_driver:
        neo4j_driver.close()
        
    print("="*80)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  To build the index:  python3 src/semantic_search.py --build [--limit N]")
        print("  To search:           python3 src/semantic_search.py \"your query\"")
        sys.exit(1)
        
    arg = sys.argv[1]
    if arg == "--build":
        limit = None
        if len(sys.argv) > 2 and sys.argv[2] == "--limit":
            try:
                limit = int(sys.argv[3])
            except Exception:
                pass
        build_index(limit)
    else:
        # Search
        search(arg)
