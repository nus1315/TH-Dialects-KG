import os
import sys
import json
import requests
import pandas as pd
from pythainlp import word_tokenize, pos_tag
from neo4j import GraphDatabase
import re

# Neo4j Details
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_AUTH = ("neo4j", "password123")
CSV_PATH = "Data/พจนานุกรมภาษาไทยถิ่น 4 ภาค (cleaned data without line breaks).xlsx - Sheet1.csv"

# Ollama Details
OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_MODEL = "qwen2.5-coder:14b"

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

# =====================================================================
# 1. PyThaiNLP POS Tagging (Heuristic Parser)
# =====================================================================
def get_pos_hint(definition):
    if not definition:
        return "ไม่พบคำใบ้"
    # Tokenize and run POS Tagging
    tokens = word_tokenize(definition, engine="newmm")
    tagged = pos_tag(tokens, corpus="orchid")
    
    triggers = ["ชื่อ", "เครื่อง", "สาร", "การ", "ความ", "สัตว์", "ต้นไม้", "ผัก", "ผลไม้", "ไม้", "อุปกรณ์", "ปลา", "นก", "ของ"]
    
    # Loop to find nouns following trigger words
    for i, (word, pos) in enumerate(tagged):
        if word in triggers and (i + 1) < len(tagged):
            next_word, next_pos = tagged[i + 1]
            if next_pos.startswith('N'):
                return next_word
            
    # Fallback: return the first noun in the sentence that isn't a trigger
    for word, pos in tagged:
        if pos.startswith('N') and word not in triggers:
            return word
            
    return "ไม่พบคำใบ้"

# =====================================================================
# 2. Local LLM (Ollama) Processing
# =====================================================================
def ask_local_llm(word, definition, pos_hint, model_name=DEFAULT_MODEL):
    prompt = f"""คุณคือผู้เชี่ยวชาญด้านภาษาศาสตร์ภาษาไทย
หน้าที่ของคุณคือวิเคราะห์ความหมายของคำศัพท์ที่กำหนดให้ แล้วสรุปคำที่เป็น "หมวดหมู่หลัก" (Hypernym หรือ Superclass) ของคำศัพท์นั้นเพียง 1 คำสั้นๆ เท่านั้น (เช่น มะม่วง -> ต้นไม้, ฉลาม -> ปลา, ช้อน -> เครื่องครัว)

ข้อมูลคำศัพท์:
- คำศัพท์: {word}
- ความหมาย: {definition}
- คำใบ้ทางไวยากรณ์ (คำนามที่ค้นพบจากความหมาย): {pos_hint}

กฎเหล็ก:
1. เลือกคำที่เป็นหมวดหมู่ใหญ่ที่ครอบคลุมคำนี้ เพียงคำเดียวสั้นๆ เท่านั้น (เช่น "ผัก", "ปลา", "เครื่องดนตรี", "เครื่องใช้ไฟฟ้า", "สัตว์")
2. ตอบกลับเป็นรูปแบบ JSON เท่านั้น ห้ามเขียนคำนำ อธิบาย หรือมีเนื้อหาอื่นนอกโครงสร้าง JSON โดยเด็ดขาด

ตัวอย่างการตอบกลับ:
{{
    "hypernym": "ชื่อหมวดหมู่หลัก"
}}
"""

    payload = {
        "model": model_name,
        "prompt": prompt,
        "stream": False,
        "format": "json"
    }
    
    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=120)
        response.raise_for_status()
        result_json = json.loads(response.json()['response'])
        hypernym = clean_word(result_json.get("hypernym", ""))
        
        # If response was empty, fallback
        if not hypernym or hypernym == "ชื่อหมวดหมู่หลัก":
            return pos_hint if pos_hint != "ไม่พบคำใบ้" else "สิ่งของ"
        return hypernym
    except Exception as e:
        print(f"  [LLM Warning/Error]: {e}. Using fallback POS Hint.")
        # Return fallback hint if LLM fails
        return pos_hint if pos_hint != "ไม่พบคำใบ้" else "สิ่งของ"

# =====================================================================
# 3. Neo4j Ingestion
# =====================================================================
def insert_to_neo4j(driver, word, pos, definition, hypernym):
    query = """
    MERGE (w1:Word {text: $word})
    ON CREATE SET w1.part_of_speech = $pos, w1.meaning = $definition
    
    MERGE (w2:Word {text: $hypernym})
    
    MERGE (w1)-[r:IS_A]->(w2)
    RETURN w1.text, w2.text
    """
    try:
        with driver.session() as session:
            session.run(query, word=word, pos=pos, definition=definition, hypernym=hypernym)
        return True
    except Exception as e:
        print(f"  [Neo4j Ingestion Error]: {e}")
        return False

# =====================================================================
# 4. Hybrid Pipeline Process
# =====================================================================
def run_hybrid_pipeline(raw_data, driver, model_name=DEFAULT_MODEL):
    print("\n" + "="*80)
    print(f"🚀 RUNNING HYBRID ONTOLOGY PIPELINE (Model: {model_name})")
    print("="*80)
    
    success_count = 0
    for idx, item in enumerate(raw_data):
        word = item['word']
        pos = item['pos']
        definition = item['definition']
        
        print(f"\n[{idx+1}/{len(raw_data)}] Processing Word: '{word}'")
        print(f"  📖 Definition: {definition}")
        
        # Step 1: PyThaiNLP POS Hint
        hint = get_pos_hint(definition)
        print(f"  🧠 Step 1 (PyThaiNLP Hint): '{hint}'")
        
        # Step 2: LLM Refinement
        final_hypernym = ask_local_llm(word, definition, hint, model_name)
        print(f"  🤖 Step 2 (Ollama Hypernym): '{final_hypernym}'")
        
        # Step 3: Neo4j Ingestion
        status = insert_to_neo4j(driver, word, pos, definition, final_hypernym)
        if status:
            print("  🔗 Step 3: Successfully linked (Word)-[:IS_A]->(Hypernym) in Neo4j!")
            success_count += 1
            
    print("\n" + "="*80)
    print(f"✅ PIPELINE COMPLETED: Successfully processed and linked {success_count}/{len(raw_data)} words!")
    print("="*80)

def load_real_csv_data(limit=10):
    print(f"Loading real dialect entries from {CSV_PATH}...")
    try:
        df = pd.read_csv(CSV_PATH)
    except Exception as e:
        print(f"Error reading CSV: {e}")
        return []
        
    df = df.dropna(subset=['ความหมาย'])
    data = []
    
    # Take a sample of real rows
    for index, row in df.head(limit).iterrows():
        meaning = clean_word(row['ความหมาย'])
        pos = clean_word(row['ชนิดของคำ']) if not pd.isna(row['ชนิดของคำ']) else "น."
        
        # Collect dialect words
        words = []
        for col in ['กลาง', 'ใต้', 'เหนือ', 'อีสาน']:
            col_words = parse_dialect_column(row[col])
            words.extend(col_words)
            
        if not words:
            continue
            
        # Add the first unique word representation
        word = words[0]
        data.append({
            "word": word,
            "pos": pos,
            "definition": meaning
        })
    return data

# =====================================================================
# Main Execution
# =====================================================================
if __name__ == "__main__":
    # Check for CLI arguments
    use_csv = "--csv" in sys.argv
    limit = 10
    
    # Parse limit if provided
    if "--limit" in sys.argv:
        try:
            limit_idx = sys.argv.index("--limit")
            limit = int(sys.argv[limit_idx + 1])
        except Exception:
            pass

    # Detect available model name on this system
    model_to_use = DEFAULT_MODEL
    # Check what model we have
    try:
        models_response = requests.get("http://localhost:11434/api/tags").json()
        available_names = [m['name'] for m in models_response.get('models', [])]
        print(f"Ollama connected successfully. Available models: {available_names}")
        
        # Select best model available
        if DEFAULT_MODEL not in available_names:
            if "deepseek-r1:14b" in available_names:
                model_to_use = "deepseek-r1:14b"
                print("Default Qwen2.5-coder:14b not found. Switching to DeepSeek-R1:14b.")
            elif available_names:
                model_to_use = available_names[0]
                print(f"Default model not found. Switching to first available: '{model_to_use}'")
    except Exception:
        print("Warning: Could not connect to Ollama server. Will use fallbacks directly.")

    # Select dataset
    if use_csv:
        raw_dictionary = load_real_csv_data(limit)
    else:
        # Default mock dataset provided by user
        raw_dictionary = [
            {"word": "กะหล่ำปลี", "pos": "น.", "definition": "ชื่อผักล้มลุกชนิดหนึ่งใบห่อรวมกันเป็นหัวกลม"},
            {"word": "กระดานดำ", "pos": "น.", "definition": "เครื่องเขียนชนิดหนึ่งทำด้วยไม้ทาสีดำสำหรับใช้ชอล์กเขียน"},
            {"word": "ไมโครเวฟ", "pos": "น.", "definition": "เครื่องใช้ไฟฟ้าที่ทำความร้อนด้วยคลื่นความถี่สูง"},
            {"word": "ผัก", "pos": "น.", "definition": "พืชที่ใช้เป็นอาหาร มักหมายถึงใบ ลำต้น หรือราก"},
            {"word": "ฉลาม", "pos": "น.", "definition": "ปลาทะเลกระดูกอ่อนชนิดหนึ่งมีฟันแหลมคมเป็นสัตว์กินเนื้อ"},
            {"word": "กล้วยไม้", "pos": "น.", "definition": "ชื่อต้นไม้ชนิดหนึ่งเกาะอยู่ตามต้นไม้อื่นมีดอกสวยงามหลากสี"}
        ]

    # Initialize Neo4j
    print(f"Connecting to Neo4j at {NEO4J_URI}...")
    try:
        driver = GraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)
        driver.verify_connectivity()
    except Exception as e:
        print(f"Failed to connect to Neo4j: {e}")
        sys.exit(1)

    # Run Pipeline
    run_hybrid_pipeline(raw_dictionary, driver, model_to_use)
    
    driver.close()
