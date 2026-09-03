# Model evaluation

Default candidate: official Qwen3-8B Q4_K_M through llama.cpp, with a 32,768-token context.
Transcription defaults to faster-whisper medium.

A live Qwen model has not been downloaded by the repository setup or automated test suite. This is
intentional: the GGUF is about 5 GB. Before production acceptance, run several reviewed meetings
and record:

- schema success before and after the one repair attempt;
- unsupported owner/date/attendance claims;
- decision-versus-proposal errors;
- timestamp evidence accuracy;
- proper-noun accuracy in Spanish;
- time to first token and total processing time;
- peak RAM/VRAM;
- human corrections per meeting.

Promote Qwen3-14B only if the 8B model misses the agreed quality threshold. Move from llama.cpp to
vLLM only when concurrent hosted jobs justify a persistent GPU service.

Model source and GGUF inventory:
https://huggingface.co/Qwen/Qwen3-8B-GGUF
