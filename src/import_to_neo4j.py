import pandas as pd
from neo4j import GraphDatabase
import re
import sys
import time
import os

# Connection details
URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
AUTH = ("neo4j", "password123")
CSV_PATH = "Data/พจนานุกรมภาษาไทยถิ่น 4 ภาค (cleaned data without line breaks).xlsx - Sheet1.csv"

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

def main():
    print(f"Reading CSV from {CSV_PATH}...")
    try:
        # Read CSV with UTF-8 encoding
        df = pd.read_csv(CSV_PATH)
    except Exception as e:
        print(f"Error reading CSV file: {e}")
        sys.exit(1)

    print(f"Successfully loaded CSV. Total rows: {len(df)}")
    print("Columns:", list(df.columns))

    # Initialize Neo4j driver
    print(f"Connecting to Neo4j at {URI}...")
    try:
        driver = GraphDatabase.driver(URI, auth=AUTH)
        driver.verify_connectivity()
        print("Connected successfully to Neo4j!")
    except Exception as e:
        print(f"Failed to connect to Neo4j: {e}")
        sys.exit(1)

    # Let's clear the database and setup constraints
    with driver.session() as session:
        print("Clearing existing data (DETACH DELETE)...")
        session.run("MATCH (n) DETACH DELETE n")

        print("Setting up unique constraints...")
        # Drop old constraints if they exist
        try:
            session.run("DROP CONSTRAINT entry_id_unique IF EXISTS")
        except Exception:
            pass
        try:
            session.run("DROP CONSTRAINT word_id_unique IF EXISTS")
        except Exception:
            pass

        try:
            # Word unique constraint based on text spelling (Only Word nodes will exist)
            session.run("CREATE CONSTRAINT word_text_unique FOR (w:Word) REQUIRE w.text IS UNIQUE")
            print("Word text unique constraint created successfully!")
        except Exception as e:
            if "AlreadyExists" not in str(e):
                print(f"Word constraint note: {e}")

    # Process and batch insert
    batch_size = 100
    batch = []
    
    start_time = time.time()
    
    print("Starting data import...")
    
    # We will iterate and construct batch payloads
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
        
        # Parse the dialect columns
        central = parse_dialect_column(row['กลาง'])
        southern = parse_dialect_column(row['ใต้'])
        northern = parse_dialect_column(row['เหนือ'])
        northeastern = parse_dialect_column(row['อีสาน'])
        
        # Determine the hub word for this concept
        hub_word = ""
        hub_dialect = ""
        
        if central:
            hub_word = central[0]
            hub_dialect = "กลาง"
        elif southern:
            hub_word = southern[0]
            hub_dialect = "ใต้"
        elif northern:
            hub_word = northern[0]
            hub_dialect = "เหนือ"
        elif northeastern:
            hub_word = northeastern[0]
            hub_dialect = "อีสาน"
            
        if not hub_word:
            # No words at all in this row, skip
            continue
            
        # Build payload item: other words point directly to this hub word
        item = {
            "entry_id": entry_id,
            "pos": pos,
            "meaning": meaning,
            "hub_word": hub_word,
            "hub_dialect": hub_dialect,
            # Filter out the hub word itself to avoid self-pointing loops
            "central": [{"text": w, "dialect": "กลาง"} for w in central if w != hub_word],
            "southern": [{"text": w, "dialect": "ใต้"} for w in southern if w != hub_word],
            "northern": [{"text": w, "dialect": "เหนือ"} for w in northern if w != hub_word],
            "northeastern": [{"text": w, "dialect": "อีสาน"} for w in northeastern if w != hub_word]
        }
        
        batch.append(item)
        
        if len(batch) >= batch_size:
            insert_batch(driver, batch)
            batch = []
            print(f"Imported {index + 1} rows...")
            
    if batch:
        insert_batch(driver, batch)
        print(f"Imported all {len(df)} rows.")

    elapsed = time.time() - start_time
    print(f"Import completed in {elapsed:.2f} seconds!")
    
    # Let's run a quick summary query
    with driver.session() as session:
        node_counts = session.run(
            "MATCH (n) RETURN labels(n)[0] AS label, count(n) AS cnt"
        ).data()
        rel_counts = session.run(
            "MATCH ()-[r]->() RETURN type(r) AS type, count(r) AS cnt"
        ).data()
        
        print("\n--- Import Summary ---")
        print("Nodes:")
        for nc in node_counts:
            print(f"  {nc['label'] or 'Unlabeled'}: {nc['cnt']}")
        print("Relationships:")
        for rc in rel_counts:
            print(f"  {rc['type']}: {rc['cnt']}")

    driver.close()

def insert_batch(driver, batch):
    # Cypher query to insert a batch of dictionary entries
    # Every node is a Word node, and other dialect words connect directly to the hub word.
    cypher_query = """
    UNWIND $batch AS row
    MERGE (hub:Word {text: row.hub_word})
    
    WITH hub, row
    
    // Central words
    FOREACH (w_obj IN row.central |
        MERGE (w:Word {text: w_obj.text})
        MERGE (w)-[r:EXPRESSES {entry_id: row.entry_id}]->(hub)
        SET r.dialect = w_obj.dialect,
            r.meaning = row.meaning,
            r.part_of_speech = row.pos
    )
    
    // Southern words
    FOREACH (w_obj IN row.southern |
        MERGE (w:Word {text: w_obj.text})
        MERGE (w)-[r:EXPRESSES {entry_id: row.entry_id}]->(hub)
        SET r.dialect = w_obj.dialect,
            r.meaning = row.meaning,
            r.part_of_speech = row.pos
    )
    
    // Northern words
    FOREACH (w_obj IN row.northern |
        MERGE (w:Word {text: w_obj.text})
        MERGE (w)-[r:EXPRESSES {entry_id: row.entry_id}]->(hub)
        SET r.dialect = w_obj.dialect,
            r.meaning = row.meaning,
            r.part_of_speech = row.pos
    )
    
    // Northeastern words
    FOREACH (w_obj IN row.northeastern |
        MERGE (w:Word {text: w_obj.text})
        MERGE (w)-[r:EXPRESSES {entry_id: row.entry_id}]->(hub)
        SET r.dialect = w_obj.dialect,
            r.meaning = row.meaning,
            r.part_of_speech = row.pos
    )
    """
    with driver.session() as session:
        session.execute_write(lambda tx: tx.run(cypher_query, batch=batch))

if __name__ == "__main__":
    main()
