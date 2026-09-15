#!/usr/bin/env python3
import asyncio
import hashlib
import json
import subprocess
import tempfile
import time
import uuid
import httpx

BASE_URL = "http://localhost:8000/api/v1"
HEADERS = {
    "Authorization": "Bearer dev-token",
    "Content-Type": "application/json",
}

def generate_test_media():
    # Generate 1-second silent valid mp4
    mp4_file = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "color=c=blue:s=320x240:d=1",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
        "-t", "1",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        mp4_file.name
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with open(mp4_file.name, "rb") as f:
        mp4_bytes = f.read()

    # Generate valid 1-page PDF using pypdf
    from io import BytesIO
    from pypdf import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buf = BytesIO()
    writer.write(buf)
    pdf_bytes = buf.getvalue()
    return mp4_bytes, pdf_bytes

async def main():
    async with httpx.AsyncClient(timeout=30.0) as client:
        print("[1] Getting Current User (/api/v1/me)...")
        res = await client.get(f"{BASE_URL}/me", headers=HEADERS)
        assert res.status_code == 200, f"Failed /me: {res.text}"
        user = res.json()
        print(f" -> User ID: {user['id']}, Name: {user['display_name']}, Email: {user['email']}")

        print("\n[2] Creating Team (/api/v1/teams)...")
        team_name = f"Competition Pitch Team {uuid.uuid4().hex[:6]}"
        res = await client.post(
            f"{BASE_URL}/teams",
            headers={**HEADERS, "Idempotency-Key": str(uuid.uuid4())},
            json={"name": team_name}
        )
        assert res.status_code == 201, f"Failed create team: {res.text}"
        team = res.json()
        team_id = team["id"]
        print(f" -> Team ID: {team_id}, Name: {team['name']}")

        print(f"\n[3] Creating Project (/api/v1/teams/{team_id}/projects)...")
        res = await client.post(
            f"{BASE_URL}/teams/{team_id}/projects",
            headers={**HEADERS, "Idempotency-Key": str(uuid.uuid4())},
            json={"name": "NextGen AI Presentation", "description": "Our competition pitch deck"}
        )
        assert res.status_code == 201, f"Failed create project: {res.text}"
        project = res.json()
        project_id = project["id"]
        print(f" -> Project ID: {project_id}, Name: {project['name']}")

        print("\n[4] Generating and Uploading Presentation Video...")
        mp4_bytes, pdf_bytes = generate_test_media()
        mp4_sha = f"sha256:{hashlib.sha256(mp4_bytes).hexdigest()}"
        mp4_size = len(mp4_bytes)

        # Upload Intent
        res = await client.post(
            f"{BASE_URL}/projects/{project_id}/assets/upload-intents",
            headers={**HEADERS, "Idempotency-Key": str(uuid.uuid4())},
            json={
                "kind": "presentation_video",
                "file_name": "pitch_video.mp4",
                "declared_media_type": "video/mp4",
                "declared_size_bytes": mp4_size
            }
        )
        assert res.status_code == 201, f"Failed upload intent video: {res.text}"
        video_intent = res.json()
        video_asset_id = video_intent["asset_id"]
        video_version_id = video_intent["asset_version_id"]
        upload_url = video_intent["upload_url"]
        print(f" -> Video Asset: {video_asset_id}, Version: {video_version_id}")

        upload_headers = {**video_intent.get("required_headers", {}), "Content-Length": str(mp4_size)}
        res = await client.put(upload_url, content=mp4_bytes, headers=upload_headers)
        assert res.status_code in (200, 204), f"Failed PUT video: {res.status_code} ({res.text})"
        print(" -> Video bytes uploaded to MinIO")

        # Complete
        res = await client.post(
            f"{BASE_URL}/assets/{video_asset_id}/versions/{video_version_id}/complete",
            headers={**HEADERS, "Idempotency-Key": str(uuid.uuid4())},
            json={"checksum": mp4_sha, "size_bytes": mp4_size}
        )
        assert res.status_code == 202, f"Failed complete video: {res.text}"
        video_asset = res.json()
        print(f" -> Video verified! State: {video_asset.get('state', 'verified')}")

        print("\n[5] Generating and Uploading Supporting PDF Document...")
        pdf_sha = f"sha256:{hashlib.sha256(pdf_bytes).hexdigest()}"
        pdf_size = len(pdf_bytes)

        res = await client.post(
            f"{BASE_URL}/projects/{project_id}/assets/upload-intents",
            headers={**HEADERS, "Idempotency-Key": str(uuid.uuid4())},
            json={
                "kind": "supporting_document",
                "file_name": "pitch_deck.pdf",
                "declared_media_type": "application/pdf",
                "declared_size_bytes": pdf_size
            }
        )
        assert res.status_code == 201, f"Failed upload intent pdf: {res.text}"
        pdf_intent = res.json()
        pdf_asset_id = pdf_intent["asset_id"]
        pdf_version_id = pdf_intent["asset_version_id"]

        upload_headers = {**pdf_intent.get("required_headers", {}), "Content-Length": str(pdf_size)}
        res = await client.put(pdf_intent["upload_url"], content=pdf_bytes, headers=upload_headers)
        assert res.status_code in (200, 204), f"Failed PUT pdf: {res.status_code} ({res.text})"
        print(" -> PDF bytes uploaded to MinIO")

        res = await client.post(
            f"{BASE_URL}/assets/{pdf_asset_id}/versions/{pdf_version_id}/complete",
            headers={**HEADERS, "Idempotency-Key": str(uuid.uuid4())},
            json={"checksum": pdf_sha, "size_bytes": pdf_size}
        )
        assert res.status_code == 202, f"Failed complete pdf: {res.text}"
        print(" -> PDF verified!")

        print("\n[6] Creating Practice Session (/api/v1/projects/.../practice-sessions)...")
        res = await client.post(
            f"{BASE_URL}/projects/{project_id}/practice-sessions",
            headers={**HEADERS, "Idempotency-Key": str(uuid.uuid4())},
            json={
                "name": "Competition Demo Session",
                "presentation_asset_version_id": video_version_id,
                "supporting_document_version_ids": [pdf_version_id],
                "rubric": {"rubric_id": "startup_pitch", "version": 1}
            }
        )
        assert res.status_code == 201, f"Failed create practice session: {res.text}"
        session = res.json()
        session_id = session["id"]
        print(f" -> Session ID: {session_id}, Status: {session['status']}, Version: {session.get('version', 1)}")

        print("\n[6b] Transitioning Session to Ready (PATCH /practice-sessions/...)...")
        res = await client.patch(
            f"{BASE_URL}/practice-sessions/{session_id}",
            headers={**HEADERS, "If-Match": f'"{session.get("version", 1)}"'},
            json={"name": session["name"]}
        )
        assert res.status_code == 200, f"Failed patch session to ready: {res.text}"
        session = res.json()
        print(f" -> Session Status after PATCH: {session['status']}, Version: {session.get('version')}")

        print(f"\n[7] Starting Analysis Attempt (/api/v1/practice-sessions/{session_id}/analysis-attempts)...")
        res = await client.post(
            f"{BASE_URL}/practice-sessions/{session_id}/analysis-attempts",
            headers={**HEADERS, "Idempotency-Key": str(uuid.uuid4())},
            json={"consent": {"accepted": True, "policy_version": 1}}
        )
        assert res.status_code == 202, f"Failed start analysis: {res.text}"
        attempt = res.json()
        attempt_id = attempt["id"]
        print(f" -> Analysis Attempt ID: {attempt_id}, Attempt Number: {attempt.get('attempt_number', attempt.get('analysis_attempt', 1))}")

        print("\n[8] Waiting for AI Worker to analyze session and prepare questions...")
        status = None
        for poll in range(30):
            await asyncio.sleep(1)
            res = await client.get(f"{BASE_URL}/practice-sessions/{session_id}", headers=HEADERS)
            session_data = res.json()
            status = session_data.get("status")
            print(f"   [poll {poll+1}] Status: {status}")
            if status in ("questions_ready", "questions_in_progress", "in_qa", "qa_ready"):
                break
        assert status in ("questions_ready", "questions_in_progress", "in_qa", "qa_ready"), f"Session failed to reach questions_ready: {status}"
        print(f" -> Practice Session reached {status}!")

        # Re-fetch session to ensure latest ETag / version
        res = await client.get(f"{BASE_URL}/practice-sessions/{session_id}", headers=HEADERS)
        session_data = res.json()
        current_version = session_data.get("version", 1)

        print(f"\n[9] Setting Speaker Mappings (version={current_version})...")
        res = await client.put(
            f"{BASE_URL}/practice-sessions/{session_id}/speaker-mappings",
            headers={**HEADERS, "If-Match": f'"{current_version}"'},
            json={
                "mappings": [
                    {"speaker_label": "SPEAKER_00", "user_id": user["id"]}
                ]
            }
        )
        print(f" -> Speaker mapping status: {res.status_code}")
        assert res.status_code in (200, 204), f"Speaker mapping failed: {res.text}"

        print(f"\n[10] Fetching Q&A Round (/api/v1/practice-sessions/{session_id}/qa)...")
        res = await client.get(f"{BASE_URL}/practice-sessions/{session_id}/qa", headers=HEADERS)
        assert res.status_code == 200, f"Failed get QA: {res.text}"
        qa_data = res.json()
        questions = qa_data.get("questions", [])
        print(f" -> Found {len(questions)} Questions!")
        for i, q in enumerate(questions):
            print(f"    Q{i+1} [{q['id']}]: {q.get('text', q.get('prompt'))} (state: {q.get('state')})")

        print("\n[11] Progressing Q&A by submitting an audio answer for Q1 and skipping remainder...")
        answered_first = False
        while True:
            res = await client.get(f"{BASE_URL}/practice-sessions/{session_id}/qa", headers=HEADERS)
            qa_data = res.json()
            curr_q_id = qa_data.get("current_question_id")
            round_state = qa_data.get("state")
            print(f" -> QA round state: {round_state}, current_question_id: {curr_q_id}")
            if not curr_q_id or round_state == "completed":
                print(" -> All questions in the round have been processed!")
                break

            if not answered_first:
                # Generate answer audio
                ans_mp4_path = "/tmp/virtujudge_answer.mp4"
                subprocess.run(
                    ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-c:a", "aac", "-b:a", "128k", ans_mp4_path],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                with open(ans_mp4_path, "rb") as f:
                    ans_bytes = f.read()
                ans_sha = f"sha256:{hashlib.sha256(ans_bytes).hexdigest()}"
                ans_size = len(ans_bytes)

                # Answer question 1 with audio
                print(f"   -> Submitting audio answer for question {curr_q_id}...")
                ans_intent_res = await client.post(
                    f"{BASE_URL}/questions/{curr_q_id}/answer-upload-intents",
                    headers={**HEADERS, "Idempotency-Key": str(uuid.uuid4())},
                    json={
                        "file_name": "answer_audio.mp4",
                        "declared_media_type": "audio/mp4",
                        "declared_size_bytes": ans_size,
                    }
                )
                assert ans_intent_res.status_code == 201, f"Failed create answer intent: {ans_intent_res.text}"
                intent_body = ans_intent_res.json()
                answer_id = intent_body["answer"]["id"]
                upload_url = intent_body["upload_intent"]["upload_url"]

                # Upload answer audio to MinIO
                put_ans = await client.put(
                    upload_url,
                    content=ans_bytes,
                    headers={"Content-Type": "audio/mp4", "If-None-Match": "*"}
                )
                assert put_ans.status_code in (200, 201), f"Failed to upload answer audio: {put_ans.status_code}"
                print(f"   -> Uploaded audio to MinIO for answer {answer_id}")

                # Complete audio asset version
                ans_asset_id = intent_body["upload_intent"]["asset_id"]
                ans_version_id = intent_body["upload_intent"]["asset_version_id"]
                comp_res = await client.post(
                    f"{BASE_URL}/assets/{ans_asset_id}/versions/{ans_version_id}/complete",
                    headers={**HEADERS, "Idempotency-Key": str(uuid.uuid4())},
                    json={"checksum": ans_sha, "size_bytes": ans_size}
                )
                assert comp_res.status_code == 202, f"Failed complete answer audio: {comp_res.text}"
                print(f"   -> Answer audio asset verified!")

                # Submit answer
                sub_res = await client.post(
                    f"{BASE_URL}/answers/{answer_id}/submit",
                    headers={**HEADERS, "Idempotency-Key": str(uuid.uuid4())},
                    json={
                        "checksum": ans_sha,
                        "size_bytes": ans_size,
                    }
                )
                assert sub_res.status_code in (200, 202), f"Failed to submit answer: {sub_res.text}"
                print(f"   -> Answer {answer_id} submitted successfully (Status: {sub_res.status_code})")
                answered_first = True

                # Wait for AI worker to analyze answer and advance to next question
                for poll_ans in range(30):
                    await asyncio.sleep(1)
                    res_qa_poll = await client.get(f"{BASE_URL}/practice-sessions/{session_id}/qa", headers=HEADERS)
                    qa_poll_data = res_qa_poll.json()
                    new_q_id = qa_poll_data.get("current_question_id")
                    if new_q_id != curr_q_id or qa_poll_data.get("state") == "completed":
                        print(f"   -> Answer analyzed! New active question: {new_q_id} (Round state: {qa_poll_data.get('state')})")
                        break
            else:
                # Skip the current active question
                res = await client.post(
                    f"{BASE_URL}/questions/{curr_q_id}/skip",
                    headers={**HEADERS, "Idempotency-Key": str(uuid.uuid4())},
                    json={"reason": "Automated competition evaluation test"}
                )
                assert res.status_code in (200, 202), f"Failed to skip question {curr_q_id}: {res.text}"
                print(f"   -> Skipped active question {curr_q_id} successfully (Status: {res.status_code})")
                await asyncio.sleep(0.5)

        print("\n[12] Checking Session Status after completing Q&A (Waiting for report generation)...")
        status = None
        for poll in range(40):
            await asyncio.sleep(1)
            res = await client.get(f"{BASE_URL}/practice-sessions/{session_id}", headers=HEADERS)
            status = res.json().get("status")
            print(f"   [poll {poll+1}] Status: {status}")
            if status in ("completed", "report_ready", "evaluation_ready"):
                break

        assert status in ("completed", "report_ready", "evaluation_ready"), f"Session failed to reach completed/report_ready: {status}"
        print(f" -> Practice Session reached terminal state: {status}!")

        print(f"\n[13] Retrieving Evaluation & Report (/api/v1/practice-sessions/{session_id}/...)...")
        res_eval = await client.get(f"{BASE_URL}/practice-sessions/{session_id}/evaluation", headers=HEADERS)
        print(f" -> Evaluation endpoint status: {res_eval.status_code}")
        assert res_eval.status_code == 200, f"Failed to retrieve evaluation: {res_eval.text}"
        eval_data = res_eval.json()
        print(f"    Evaluation ID: {eval_data.get('id')}, Score: {eval_data.get('overall_score')}")

        res_rep = await client.get(f"{BASE_URL}/practice-sessions/{session_id}/report", headers=HEADERS)
        print(f" -> Report endpoint status: {res_rep.status_code}")
        assert res_rep.status_code == 200, f"Failed to retrieve report: {res_rep.text}"
        rep_data = res_rep.json()
        print(f"    Report ID: {rep_data.get('report_id')}, Title: {rep_data.get('title')}, Score: {rep_data.get('overall_score')}")

        print(f"\n[14] Requesting PDF Export (/api/v1/practice-sessions/{session_id}/report/pdf)...")
        res_pdf = await client.post(
            f"{BASE_URL}/practice-sessions/{session_id}/report/pdf",
            headers={**HEADERS, "Idempotency-Key": str(uuid.uuid4())},
        )
        print(f" -> PDF export status: {res_pdf.status_code}")
        assert res_pdf.status_code in (200, 202), f"PDF export failed: {res_pdf.text}"
        export_info = res_pdf.json()
        export_id = export_info.get("id") or export_info.get("export_id")
        print(f" -> Export created! ID: {export_id}")
        assert export_id, "Export ID not returned"
        for poll in range(15):
            await asyncio.sleep(1)
            res_status = await client.get(f"{BASE_URL}/report-exports/{export_id}", headers=HEADERS)
            assert res_status.status_code == 200, f"Failed get export status: {res_status.text}"
            exp_data = res_status.json()
            exp_state = exp_data.get("status") or exp_data.get("state")
            print(f"    [poll {poll+1}] Export State: {exp_state}")
            if exp_state in ("completed", "ready", "succeeded"):
                break

        print(f"\n[15] Requesting PDF Download Intent (/api/v1/report-exports/{export_id}/download-intents)...")
        res_intent = await client.post(
            f"{BASE_URL}/report-exports/{export_id}/download-intents",
            headers=HEADERS,
        )
        assert res_intent.status_code == 200, f"Failed to create download intent: {res_intent.text}"
        intent_data = res_intent.json()
        download_url = intent_data.get("download_url")
        print(f" -> Presigned Download URL: {download_url}")
        assert download_url, "Download URL not returned"

        # Download the PDF bytes
        dl_res = await client.get(download_url)
        assert dl_res.status_code == 200, f"Failed to download PDF bytes: {dl_res.status_code}"
        print(f" -> Successfully downloaded PDF ({len(dl_res.content)} bytes, magic bytes: {dl_res.content[:4]})")
        assert dl_res.content.startswith(b"%PDF"), "Downloaded file is not a valid PDF"

        print("\n=======================================================")
        print(" [SUCCESS] FULL END-TO-END WORKFLOW VERIFIED LOCALLY! ")
        print("=======================================================")

if __name__ == "__main__":
    asyncio.run(main())
