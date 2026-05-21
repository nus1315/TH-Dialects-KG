# 🌍 TH-Dialects-KG (ระบบพจนานุกรมภาษาถิ่น 4 ภาค และกราฟความรู้)

ระบบวิเคราะห์และคลังข้อมูลภาษาไทยถิ่น 4 ภาค (กลาง, ใต้, เหนือ, อีสาน) ในรูปแบบ **กราฟความรู้ (Knowledge Graph)** ทำงานร่วมกับระบบสืบค้นเชิงความหมาย (Semantic Search) และการสกัดหมวดหมู่หลัก (Ontology) แบบไฮบริดด้วยภาษาศาสตร์ร่วมกับปัญญาประดิษฐ์ท้องถิ่น (Local LLM)

---

## 🛠️ สถาปัตยกรรมระบบ (Architecture)

โปรเจกต์นี้ทำงานร่วมกันผ่าน 3 เสาหลักทางวิศวกรรมข้อมูล:
1. **Neo4j Graph Database (Word-Centric Structure):**
   * ข้อมูลถูกแปลงเป็นโครงสร้างแบบ Word-Centric ซึ่งสลัดรูปแบบวงลูปคู่ออกไป โดยคำแต่ละคำจะมีโหนด `Word` เป็นของตัวเอง และระบุความหมาย/ภาคถิ่นลงบนความสัมพันธ์ `[:EXPRESSES]` ทำให้กราฟคลีน สวยงาม และค้นหาความเชื่อมโยงย้อนกลับได้สมบูรณ์
2. **Vector Semantic Search (BGE-M3 + FAISS):**
   * ใช้โมเดล **`BAAI/bge-m3`** เพื่อแปลงคำจำกัดความความหมาย (Definitions) ทั้งหมดในพจนานุกรมให้เป็นเวกเตอร์ 1,024 มิติ
   * ค้นหาคำศัพท์ด้วยเวกเตอร์ความเร็วสูงโดยใช้ **`FAISS`** จากนั้นจะดึงข้อมูลเชื่อมโยงไปยัง Neo4j เพื่อนำคำพ้อง (Synonyms) ของทุกภาคขึ้นมาแสดงแบบ Real-time
3. **Hybrid Ontology Pipeline (PyThaiNLP + Local LLM):**
   * **สกัดไวยากรณ์ก่อน (Rule-Based):** ใช้ `PyThaiNLP` ตัดคำและแกะชนิดคำ (POS) เพื่อหาคำใบ้ล่วงหน้าจากรูปแบบความหมาย เช่น คำที่อยู่หลัง trigger word เช่น "ชื่อ", "เครื่อง", "สัตว์"
   * **ขัดเกลาด้วย LLM (Semantic Refinement):** นำผลลัพธ์พร้อมประโยคและความหมายส่งเข้า **Ollama (Qwen2.5 / DeepSeek)** เพื่อบีบให้ปัญญาประดิษฐ์คืนค่ากลุ่มหลัก (Hypernym) ที่ถูกต้องที่สุดในรูปแบบ JSON
   * **เชื่อมโยงเข้าฐานข้อมูล:** นำโครงสร้างลำดับชั้นที่ได้เขียนกลับเข้า Neo4j เกิดเป็นเส้นเชื่อมความสัมพันธ์ `(Word)-[:IS_A]->(Hypernym)` โดยอัตโนมัติ

---

## 🐳 วิธีเริ่มต้นใช้งานผ่าน Docker (แนะนำและสะดวกที่สุด)

คุณสามารถเริ่มรันทั้งฐานข้อมูล Neo4j และแอปพลิเคชันสภาพแวดล้อมได้ในคำสั่งเดียว:

```bash
# เริ่มระบบ Neo4j Database และ Application container
docker compose up -d
```

* **Neo4j Browser:** เข้าใช้งานผ่านหน้าเว็บได้ที่ `http://localhost:7474` (รหัสผ่านเริ่มต้น: `neo4j` / `password123`)
* **App Container:** เข้าใช้งานเพื่อทำสิ่งต่างๆ ผ่าน Docker Shell:
  ```bash
  docker compose exec app bash
  ```

---

## 💻 วิธีติดตั้งบนเครื่องโลคอล (Local Setup)

หากต้องการติดตั้งและรันแบบ Native บนเครื่องของคุณเอง:

### 1. โคลนรีโพสิทอรี
```bash
git clone git@github.com:nus1315/TH-Dialects-KG.git
cd TH-Dialects-KG
```

### 2. สร้าง Virtual Environment และติดตั้งไลบรารี
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. เปิดใช้งาน Ollama Local LLM
ตรวจสอบให้แน่ใจว่าติดตั้ง [Ollama](https://ollama.com) และรันเซิร์ฟเวอร์เรียบร้อยแล้ว:
```bash
# ตรวจสอบรุ่นโมเดลและดึงรุ่นที่แนะนำมาใช้งาน
ollama pull qwen2.5-coder:14b
```

---

## 🚀 วิธีการใช้งานโปรแกรม (How to Run)

หลังจากเข้าสู่ Virtual Environment หรือรันแอปพลิเคชันเสร็จแล้ว:

### 1. นำข้อมูลพจนานุกรมดิบเข้าสู่ Neo4j
สคริปต์นี้จะอ่านข้อมูลจาก CSV และสร้างโหนด Word-Centric กราฟใน Neo4j:
```bash
python3 src/import_to_neo4j.py
```

### 2. คอมไพล์ดัชนีเวกเตอร์สำหรับระบบ Semantic Search
ประมวลผลความหมายของคำจำกัดความทั้งหมดเป็นเวกเตอร์ฐานข้อมูล FAISS:
```bash
# คอมไพล์จำกัดจำนวนแถวสำหรับการทดสอบด่วนบน CPU (แนะนำในขั้นเริ่มต้น)
python3 src/semantic_search.py --build --limit 300

# คอมไพล์ข้อมูลทั้งหมด (แนะนำเมื่อรันผ่านการ์ดจอ GPU)
python3 src/semantic_search.py --build
```

### 3. ค้นหาคำศัพท์เชิงสมานตศาสตร์ (Semantic Reversed Dictionary Search)
พิมพ์ความหมายที่คุณนึกออก โปรแกรมจะไปหาคำพ้องภาษาถิ่น 4 ภาคมาให้คุณทันที:
```bash
python3 src/semantic_search.py "อุปกรณ์ตักอาหาร"
```

### 4. รันระบบสกัดหมวดหมู่หลักระดับไฮบริด (Hybrid Ontology Ingestion)
ใช้ PyThaiNLP จับมือกับ Ollama เพื่อสกัดและประกอบความสัมพันธ์แบบ Taxonomy ลำดับชั้น:
```bash
# รันข้อมูลทดสอบจากประโยคจำลองหลัก
python3 src/hybrid_ontology.py

# รันดึงตัวอย่างแถวจริงจากไฟล์พจนานุกรมหลัก 10 แถวมาเขียนลงกราฟ Neo4j
python3 src/hybrid_ontology.py --csv --limit 10
```

---

## 📁 โครงสร้างโปรเจกต์ (Project Structure)
```text
TH-Dialects-KG/
├── Data/                      # โฟลเดอร์เก็บข้อมูลดิบและดัชนีเวกเตอร์
│   ├── meanings.index         # ดัชนี FAISS Vector (สร้างหลังรันบิลด์)
│   ├── meanings_metadata.json # เมทาดาตาสำหรับแมปคำ
│   └── พจนานุกรมภาษาไทยถิ่น 4 ภาค...xlsx - Sheet1.csv
├── src/                       # ซอร์สโค้ดหลักของโปรเจกต์
│   ├── import_to_neo4j.py     # นำเข้าพจนานุกรมเชื่อมโยง Word-Centric Graph
│   ├── semantic_search.py     # ระบบค้นหาย้อนกลับด้วยเวกเตอร์ FAISS & Graph
│   └── hybrid_ontology.py     # ท่อรันสกัด Hypernym ร่วมระหว่าง PyThaiNLP + LLM
├── Dockerfile                 # อิมเมจแอปพลิเคชันหลัก
├── docker-compose.yml         # ควบคุม Neo4j และแอปพลิเคชันทำงานร่วมกัน
├── requirements.txt           # รายการแพ็กเกจไลบรารีระบบ
└── .gitignore                 # กำหนดค่าละเว้นแคชไฟล์ขนาดใหญ่
```
