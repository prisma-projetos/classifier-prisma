from __future__ import annotations

from datetime import datetime, timezone

from payload import ScraperPayload
from pipeline import ClassificationResult

# HISTÓRICO: entre 2026-08-28 e 2026-08-29 este módulo também expunha
# `make_rabbitmq_notifier` (publicação DIRETA e SÍNCRONA logo após
# persistir/comitar em `tabela_classificacao`, decisão da ADR 0003). Isso foi
# revisto pela ADR 0004 (2026-08-29): a notificação volta a passar por um
# outbox transacional -- agora `classifier_analyser_outbox`, tabela de uso
# interno do Classifier dentro do próprio `app_db2` (diferente do outbox
# antigo da ADR 0002, que previa uma tabela no schema compartilhado gerenciado
# por outra pessoa). `src/message_handler.py` não publica mais nada
# diretamente -- só grava o payload (via `build_notification_payload`,
# abaixo) na mesma transação da classificação; quem publica de fato é
# `src/notify_analyser.py` (processo separado, agendável via cron), via
# `src/notification_publisher.py` + `src/notifications_store.py`.
#
# `make_rabbitmq_notifier` foi removida daqui por não ter mais chamador (não
# é órfã documentada de propósito, como os outros três módulos citados acima
# eram entre as ADRs 0003 e 0004 -- é código morto de verdade, sem uso
# planejado). A infraestrutura de conexão que ela usava
# (`rabbitmq_publisher.RabbitMQPublisher`) continua em uso, só que agora só
# pelo publisher do outbox.
#
# `build_notification_payload` continua sendo a única fonte do formato do
# payload -- reaproveitada tanto para montar o que é gravado no outbox
# quanto (no schema antigo) para o payload publicado direto. Formato ainda
# é PROPOSTA, não contrato fechado com o Analyser -- ver ADR 0003.
SCHEMA_VERSION = 1


def build_notification_payload(
    payload: ScraperPayload, classifications: list[ClassificationResult]
) -> dict:
    """Monta o payload publicado para o Analyser -- baseado só no que
    `tabela_classificacao` já grava (nunca inventa campo que a tabela não
    tem). Leve de propósito (não duplica `evidence_text`/parágrafo inteiro,
    só o necessário para o Analyser saber o que mudou e ir consultar o
    System DB para o resto -- mesmo racional do outbox antigo, ver ADR
    0002) -- mas mantém `paragraph_index` para permitir ao Analyser
    localizar a linha exata em `tabela_classificacao` se precisar.

    ATENÇÃO -- CONTRATO NÃO CONFIRMADO: este formato é uma proposta razoável
    deste repositório, não uma decisão de integração fechada -- precisa ser
    validado com quem for implementar o consumidor do lado do Analyser antes
    de virar contrato definitivo (mesmo tratamento dado à fila/routing key
    em `src/rabbitmq_publisher.py`). Não assumir este formato como estável
    em produção sem essa confirmação.

    ATENÇÃO -- SEMÂNTICA DE `notificado_em` MUDOU COM O OUTBOX (ADR 0004):
    esta função roda uma vez, dentro de `handle_message`, no momento em que
    a classificação é persistida -- o payload inteiro (incluindo este
    campo) é gravado pronto em `classifier_analyser_outbox.payload` e só
    publicado de verdade depois, por um processo separado
    (`src/notify_analyser.py`). Ou seja, `notificado_em` hoje reflete
    "quando o payload foi montado/enfileirado", não "quando foi de fato
    publicado no RabbitMQ" -- pode haver um atraso entre os dois (retry,
    fila de pendentes). Registrado como pendência em
    docs/adr/0004-outbox-proprio-para-notificacao-analyser.md -- não
    renomeado aqui porque o campo já é parte do contrato não confirmado com
    o Analyser (ver aviso acima)."""
    return {
        "schema_version": SCHEMA_VERSION,
        "article_id": payload.article_id,
        "job_id": payload.job_id,
        "pipeline_version": payload.pipeline_version,
        "link_noticia": payload.canonical_link,
        "data_publicacao_noticia": payload.published_at.isoformat() if payload.published_at else None,
        "notificado_em": datetime.now(timezone.utc).isoformat(),
        "classificacoes": [
            {
                "entity_type": result.entity_type,
                "entity_id": result.entity_id,
                "paragraph_index": result.paragraph_index,
                "classificacao_sentimento": result.category,
                "score_confianca": result.confidence,
            }
            for result in classifications
        ],
    }


__all__ = ["SCHEMA_VERSION", "build_notification_payload"]
