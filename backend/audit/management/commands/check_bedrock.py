"""Makes 2 small Bedrock calls (1 embedding + 1 chat) to prove the laptop can reach Bedrock.
These calls cost a very small amount on your AWS account."""
from django.core.management.base import BaseCommand, CommandError

from core.bedrock_client import BedrockClient
from core.config import load_config


class Command(BaseCommand):
    help = "Test the Bedrock connection with one embedding call and one chat call."

    def handle(self, *args, **options):
        cfg = load_config()
        try:
            cfg.require_bedrock()
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        client = BedrockClient(cfg)
        self.stdout.write(f"Region: {cfg.aws_region}")

        self.stdout.write(f"Embedding model: {cfg.embed_model_id} ...")
        vector = client.embed("Refunds can be requested within 3 days of delivery.")
        self.stdout.write(self.style.SUCCESS(f"  OK: got a vector with {len(vector)} numbers"))

        self.stdout.write(f"Chat model: {cfg.chat_model_id} ...")
        result = client.converse(
            [{"role": "user", "content": [{"text": "Reply with exactly the word OK."}]}], max_tokens=20
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"  OK: reply={result.text!r}, input_tokens={result.input_tokens}, "
                f"output_tokens={result.output_tokens}, latency={result.latency_ms} ms"
            )
        )
