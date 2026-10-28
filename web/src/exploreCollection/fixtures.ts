/* Actual pure Python draft DTO captures; synthetic science only. */
export const pythonFixtures: Record<string, unknown> = {
  "summary": {
    "run_id": "00000000-0000-0000-0000-000000000384",
    "run_status": "succeeded",
    "explore_collection_intent": {
      "protocol": "polymath-explore-collection-v1",
      "max_bridges": 3
    },
    "stages": [
      {
        "stage_id": "00000000-0000-0000-0000-000000000064",
        "run_id": "00000000-0000-0000-0000-000000000384",
        "ordinal": 0,
        "result_id": "00000000-0000-0000-0000-0000000000c8",
        "result_sha256": "0000000000000000000000000000000000000000000000000000000000000001",
        "depth": 1,
        "assessment": {
          "novelty": "uncertain",
          "novelty_rationale": "Bounded synthetic searches establish no novelty.",
          "confidence": "tentative",
          "confidence_score": 95,
          "confidence_scope": "uncalibrated_model_judgement",
          "confidence_rationale": "Synthetic model judgement, not a calibrated probability.",
          "limitations": [
            "No real scientific truth is asserted by this fixture."
          ]
        },
        "parent_refs": [],
        "action_ordinal": 4,
        "model_request_id": "model-0004"
      },
      {
        "stage_id": "00000000-0000-0000-0000-000000000065",
        "run_id": "00000000-0000-0000-0000-000000000384",
        "ordinal": 1,
        "result_id": "00000000-0000-0000-0000-0000000000c9",
        "result_sha256": "0000000000000000000000000000000000000000000000000000000000000002",
        "depth": 1,
        "assessment": {
          "novelty": "uncertain",
          "novelty_rationale": "Bounded synthetic searches establish no novelty.",
          "confidence": "tentative",
          "confidence_score": 55,
          "confidence_scope": "uncalibrated_model_judgement",
          "confidence_rationale": "Synthetic model judgement, not a calibrated probability.",
          "limitations": [
            "No real scientific truth is asserted by this fixture."
          ]
        },
        "parent_refs": [],
        "action_ordinal": 8,
        "model_request_id": "model-0008"
      },
      {
        "stage_id": "00000000-0000-0000-0000-000000000066",
        "run_id": "00000000-0000-0000-0000-000000000384",
        "ordinal": 2,
        "result_id": "00000000-0000-0000-0000-0000000000ca",
        "result_sha256": "0000000000000000000000000000000000000000000000000000000000000003",
        "depth": 1,
        "assessment": {
          "novelty": "uncertain",
          "novelty_rationale": "Bounded synthetic searches establish no novelty.",
          "confidence": "tentative",
          "confidence_score": 15,
          "confidence_scope": "uncalibrated_model_judgement",
          "confidence_rationale": "Synthetic model judgement, not a calibrated probability.",
          "limitations": [
            "No real scientific truth is asserted by this fixture."
          ]
        },
        "parent_refs": [],
        "action_ordinal": 12,
        "model_request_id": "model-0012"
      }
    ],
    "rankings": [
      {
        "ranked": [
          {
            "reference": {
              "result_id": "00000000-0000-0000-0000-0000000000c9",
              "run_id": "00000000-0000-0000-0000-000000000384",
              "content_sha256": "0000000000000000000000000000000000000000000000000000000000000002"
            },
            "rationale": "The model prefers this weaker-scored but more useful question."
          },
          {
            "reference": {
              "result_id": "00000000-0000-0000-0000-0000000000c8",
              "run_id": "00000000-0000-0000-0000-000000000384",
              "content_sha256": "0000000000000000000000000000000000000000000000000000000000000001"
            },
            "rationale": "The higher confidence score does not determine ranking."
          }
        ],
        "excluded": [],
        "ranking_id": "00000000-0000-0000-0000-0000000003e8",
        "run_id": "00000000-0000-0000-0000-000000000384",
        "packet_sha256": "49b9afb0e8a2a4f4e20107aaf55e95bf7287f6a7715a0f329c5e869c42c061a1",
        "model_request_id": "model-0010",
        "action_ordinal": 10,
        "covered_through_ordinal": 1
      },
      {
        "ranked": [
          {
            "reference": {
              "result_id": "00000000-0000-0000-0000-0000000000ca",
              "run_id": "00000000-0000-0000-0000-000000000384",
              "content_sha256": "0000000000000000000000000000000000000000000000000000000000000003"
            },
            "rationale": "Model preference: the lowest-scored original bridge opens the most useful testable question."
          },
          {
            "reference": {
              "result_id": "00000000-0000-0000-0000-0000000000c9",
              "run_id": "00000000-0000-0000-0000-000000000384",
              "content_sha256": "0000000000000000000000000000000000000000000000000000000000000002"
            },
            "rationale": "Model preference: another useful direction, independently ordered."
          }
        ],
        "excluded": [
          {
            "reference": {
              "result_id": "00000000-0000-0000-0000-0000000000c8",
              "run_id": "00000000-0000-0000-0000-000000000384",
              "content_sha256": "0000000000000000000000000000000000000000000000000000000000000001"
            },
            "reason": "The model excluded this higher-scored mapping from its final recommendation; the evidence remains saved."
          }
        ],
        "ranking_id": "00000000-0000-0000-0000-0000000003e9",
        "run_id": "00000000-0000-0000-0000-000000000384",
        "packet_sha256": "ffc16193db767c310caf225258e0238d78a4564924129f9a7f16e00f346a7de3",
        "model_request_id": "model-0016",
        "action_ordinal": 16,
        "covered_through_ordinal": 2
      }
    ],
    "selected_final_ranking": {
      "ranked": [
        {
          "reference": {
            "result_id": "00000000-0000-0000-0000-0000000000ca",
            "run_id": "00000000-0000-0000-0000-000000000384",
            "content_sha256": "0000000000000000000000000000000000000000000000000000000000000003"
          },
          "rationale": "Model preference: the lowest-scored original bridge opens the most useful testable question."
        },
        {
          "reference": {
            "result_id": "00000000-0000-0000-0000-0000000000c9",
            "run_id": "00000000-0000-0000-0000-000000000384",
            "content_sha256": "0000000000000000000000000000000000000000000000000000000000000002"
          },
          "rationale": "Model preference: another useful direction, independently ordered."
        }
      ],
      "excluded": [
        {
          "reference": {
            "result_id": "00000000-0000-0000-0000-0000000000c8",
            "run_id": "00000000-0000-0000-0000-000000000384",
            "content_sha256": "0000000000000000000000000000000000000000000000000000000000000001"
          },
          "reason": "The model excluded this higher-scored mapping from its final recommendation; the evidence remains saved."
        }
      ],
      "ranking_id": "00000000-0000-0000-0000-0000000003e9",
      "run_id": "00000000-0000-0000-0000-000000000384",
      "packet_sha256": "ffc16193db767c310caf225258e0238d78a4564924129f9a7f16e00f346a7de3",
      "model_request_id": "model-0016",
      "action_ordinal": 16,
      "covered_through_ordinal": 2
    },
    "stop_reason": "The model explicitly finished its ranked collection.",
    "total_usage": {
      "model_calls": 20,
      "input_tokens_reported": "200",
      "output_tokens_reported": "120",
      "calls_without_usage": 0
    },
    "accounting": {
      "counted_spend_microdollars": "9007199254740993",
      "open_hold_microdollars": "500",
      "unknown_hold_microdollars": "300"
    }
  },
  "bridges": {
    "run_id": "00000000-0000-0000-0000-000000000384",
    "stages": [
      {
        "stage": {
          "stage_id": "00000000-0000-0000-0000-000000000064",
          "run_id": "00000000-0000-0000-0000-000000000384",
          "ordinal": 0,
          "result_id": "00000000-0000-0000-0000-0000000000c8",
          "result_sha256": "0000000000000000000000000000000000000000000000000000000000000001",
          "depth": 1,
          "assessment": {
            "novelty": "uncertain",
            "novelty_rationale": "Bounded synthetic searches establish no novelty.",
            "confidence": "tentative",
            "confidence_score": 95,
            "confidence_scope": "uncalibrated_model_judgement",
            "confidence_rationale": "Synthetic model judgement, not a calibrated probability.",
            "limitations": [
              "No real scientific truth is asserted by this fixture."
            ]
          },
          "parent_refs": [],
          "action_ordinal": 4,
          "model_request_id": "model-0004"
        },
        "result": {
          "status": "bridge",
          "mode": "explore",
          "finding": {
            "outcome": "bridge",
            "title": "Synthetic bridge 1",
            "field_left": "Information theory",
            "field_right": "Biology",
            "explanation": "Complete model explanation.",
            "mappings": [
              {
                "left": "Messages",
                "right": "Pathways",
                "assumption": "Comparable noise."
              }
            ],
            "open_question": "Does the analogy survive?",
            "strongest_objection": "Mechanisms differ.",
            "objection_verdict": "The analogy is unverified.",
            "novelty_limitations": "No proof or novelty established.",
            "evidence": [
              {
                "source_id": "private-source-a",
                "side": "left",
                "quote": "Exact synthetic source evidence supports a conditional analogy.",
                "supports": "Original support statement."
              },
              {
                "source_id": "private-source-b",
                "side": "right",
                "quote": "Exact synthetic source evidence supports a conditional analogy.",
                "supports": "Original support statement."
              }
            ]
          },
          "field_selections": [],
          "memory_reads": [],
          "quotes": [
            {
              "source_id": "private-source-a",
              "side": "left",
              "claim_id": null,
              "quote": "Exact synthetic source evidence supports a conditional analogy.",
              "supports": "Original support statement.",
              "start": 2,
              "end": 65,
              "source_sha256": "356b072f40e5c9754e458365fce8f60d858256dc79bcba716ba02e8cb2c1977c",
              "content_kind": "abstract"
            },
            {
              "source_id": "private-source-b",
              "side": "right",
              "claim_id": null,
              "quote": "Exact synthetic source evidence supports a conditional analogy.",
              "supports": "Original support statement.",
              "start": 2,
              "end": 65,
              "source_sha256": "356b072f40e5c9754e458365fce8f60d858256dc79bcba716ba02e8cb2c1977c",
              "content_kind": "abstract"
            }
          ],
          "sources": [
            {
              "source_id": "private-source-a",
              "title": "Synthetic cited paper",
              "url": "https://private.example.test/opaque/private-source-a?token=PRIVATE_URL_TOKEN",
              "text": "🌌 Exact synthetic source evidence supports a conditional analogy.",
              "content_kind": "abstract",
              "retrieved_at": "2026-10-28T00:00:00Z",
              "extraction_method": "private-extractor-v1"
            },
            {
              "source_id": "private-source-b",
              "title": "Independent cited paper",
              "url": "https://private.example.test/opaque/private-source-b?token=PRIVATE_URL_TOKEN",
              "text": "🌌 Exact synthetic source evidence supports a conditional analogy.",
              "content_kind": "abstract",
              "retrieved_at": "2026-10-28T00:00:00Z",
              "extraction_method": "private-extractor-v1"
            }
          ],
          "events": [
            {
              "sequence": 1,
              "kind": "private-event",
              "summary": "PRIVATE_EVENT_CONTEXT"
            }
          ],
          "usage": {
            "model_calls": 4,
            "input_tokens_reported": 20,
            "output_tokens_reported": 40,
            "calls_without_usage": 0
          },
          "error_code": null,
          "validation_scope": "source_identity_and_quote_integrity"
        },
        "parent_edges": [],
        "primary_paper_groups": [
          {
            "paper_id": "paper-1",
            "source_ids": [
              "private-source-a"
            ]
          },
          {
            "paper_id": "paper-2",
            "source_ids": [
              "private-source-b"
            ]
          }
        ],
        "status": "current_supported"
      },
      {
        "stage": {
          "stage_id": "00000000-0000-0000-0000-000000000065",
          "run_id": "00000000-0000-0000-0000-000000000384",
          "ordinal": 1,
          "result_id": "00000000-0000-0000-0000-0000000000c9",
          "result_sha256": "0000000000000000000000000000000000000000000000000000000000000002",
          "depth": 1,
          "assessment": {
            "novelty": "uncertain",
            "novelty_rationale": "Bounded synthetic searches establish no novelty.",
            "confidence": "tentative",
            "confidence_score": 55,
            "confidence_scope": "uncalibrated_model_judgement",
            "confidence_rationale": "Synthetic model judgement, not a calibrated probability.",
            "limitations": [
              "No real scientific truth is asserted by this fixture."
            ]
          },
          "parent_refs": [],
          "action_ordinal": 8,
          "model_request_id": "model-0008"
        },
        "result": {
          "status": "bridge",
          "mode": "explore",
          "finding": {
            "outcome": "bridge",
            "title": "Synthetic bridge 2",
            "field_left": "Information theory",
            "field_right": "Biology",
            "explanation": "Complete model explanation.",
            "mappings": [
              {
                "left": "Messages",
                "right": "Pathways",
                "assumption": "Comparable noise."
              }
            ],
            "open_question": "Does the analogy survive?",
            "strongest_objection": "Mechanisms differ.",
            "objection_verdict": "The analogy is unverified.",
            "novelty_limitations": "No proof or novelty established.",
            "evidence": [
              {
                "source_id": "private-source-a",
                "side": "left",
                "quote": "Exact synthetic source evidence supports a conditional analogy.",
                "supports": "Original support statement."
              },
              {
                "source_id": "private-source-b",
                "side": "right",
                "quote": "Exact synthetic source evidence supports a conditional analogy.",
                "supports": "Original support statement."
              }
            ]
          },
          "field_selections": [],
          "memory_reads": [],
          "quotes": [
            {
              "source_id": "private-source-a",
              "side": "left",
              "claim_id": null,
              "quote": "Exact synthetic source evidence supports a conditional analogy.",
              "supports": "Original support statement.",
              "start": 2,
              "end": 65,
              "source_sha256": "356b072f40e5c9754e458365fce8f60d858256dc79bcba716ba02e8cb2c1977c",
              "content_kind": "abstract"
            },
            {
              "source_id": "private-source-b",
              "side": "right",
              "claim_id": null,
              "quote": "Exact synthetic source evidence supports a conditional analogy.",
              "supports": "Original support statement.",
              "start": 2,
              "end": 65,
              "source_sha256": "356b072f40e5c9754e458365fce8f60d858256dc79bcba716ba02e8cb2c1977c",
              "content_kind": "abstract"
            }
          ],
          "sources": [
            {
              "source_id": "private-source-a",
              "title": "Synthetic cited paper",
              "url": "https://private.example.test/opaque/private-source-a?token=PRIVATE_URL_TOKEN",
              "text": "🌌 Exact synthetic source evidence supports a conditional analogy.",
              "content_kind": "abstract",
              "retrieved_at": "2026-10-28T00:00:00Z",
              "extraction_method": "private-extractor-v1"
            },
            {
              "source_id": "private-source-b",
              "title": "Independent cited paper",
              "url": "https://private.example.test/opaque/private-source-b?token=PRIVATE_URL_TOKEN",
              "text": "🌌 Exact synthetic source evidence supports a conditional analogy.",
              "content_kind": "abstract",
              "retrieved_at": "2026-10-28T00:00:00Z",
              "extraction_method": "private-extractor-v1"
            }
          ],
          "events": [
            {
              "sequence": 1,
              "kind": "private-event",
              "summary": "PRIVATE_EVENT_CONTEXT"
            }
          ],
          "usage": {
            "model_calls": 8,
            "input_tokens_reported": 20,
            "output_tokens_reported": 40,
            "calls_without_usage": 0
          },
          "error_code": null,
          "validation_scope": "source_identity_and_quote_integrity"
        },
        "parent_edges": [],
        "primary_paper_groups": [
          {
            "paper_id": "paper-1",
            "source_ids": [
              "private-source-a"
            ]
          },
          {
            "paper_id": "paper-2",
            "source_ids": [
              "private-source-b"
            ]
          }
        ],
        "status": "current_supported"
      },
      {
        "stage": {
          "stage_id": "00000000-0000-0000-0000-000000000066",
          "run_id": "00000000-0000-0000-0000-000000000384",
          "ordinal": 2,
          "result_id": "00000000-0000-0000-0000-0000000000ca",
          "result_sha256": "0000000000000000000000000000000000000000000000000000000000000003",
          "depth": 1,
          "assessment": {
            "novelty": "uncertain",
            "novelty_rationale": "Bounded synthetic searches establish no novelty.",
            "confidence": "tentative",
            "confidence_score": 15,
            "confidence_scope": "uncalibrated_model_judgement",
            "confidence_rationale": "Synthetic model judgement, not a calibrated probability.",
            "limitations": [
              "No real scientific truth is asserted by this fixture."
            ]
          },
          "parent_refs": [],
          "action_ordinal": 12,
          "model_request_id": "model-0012"
        },
        "result": {
          "status": "bridge",
          "mode": "explore",
          "finding": {
            "outcome": "bridge",
            "title": "Synthetic bridge 3",
            "field_left": "Information theory",
            "field_right": "Biology",
            "explanation": "Complete model explanation.",
            "mappings": [
              {
                "left": "Messages",
                "right": "Pathways",
                "assumption": "Comparable noise."
              }
            ],
            "open_question": "Does the analogy survive?",
            "strongest_objection": "Mechanisms differ.",
            "objection_verdict": "The analogy is unverified.",
            "novelty_limitations": "No proof or novelty established.",
            "evidence": [
              {
                "source_id": "private-source-a",
                "side": "left",
                "quote": "Exact synthetic source evidence supports a conditional analogy.",
                "supports": "Original support statement."
              },
              {
                "source_id": "private-source-b",
                "side": "right",
                "quote": "Exact synthetic source evidence supports a conditional analogy.",
                "supports": "Original support statement."
              }
            ]
          },
          "field_selections": [],
          "memory_reads": [],
          "quotes": [
            {
              "source_id": "private-source-a",
              "side": "left",
              "claim_id": null,
              "quote": "Exact synthetic source evidence supports a conditional analogy.",
              "supports": "Original support statement.",
              "start": 2,
              "end": 65,
              "source_sha256": "356b072f40e5c9754e458365fce8f60d858256dc79bcba716ba02e8cb2c1977c",
              "content_kind": "abstract"
            },
            {
              "source_id": "private-source-b",
              "side": "right",
              "claim_id": null,
              "quote": "Exact synthetic source evidence supports a conditional analogy.",
              "supports": "Original support statement.",
              "start": 2,
              "end": 65,
              "source_sha256": "356b072f40e5c9754e458365fce8f60d858256dc79bcba716ba02e8cb2c1977c",
              "content_kind": "abstract"
            }
          ],
          "sources": [
            {
              "source_id": "private-source-a",
              "title": "Synthetic cited paper",
              "url": "https://private.example.test/opaque/private-source-a?token=PRIVATE_URL_TOKEN",
              "text": "🌌 Exact synthetic source evidence supports a conditional analogy.",
              "content_kind": "abstract",
              "retrieved_at": "2026-10-28T00:00:00Z",
              "extraction_method": "private-extractor-v1"
            },
            {
              "source_id": "private-source-b",
              "title": "Independent cited paper",
              "url": "https://private.example.test/opaque/private-source-b?token=PRIVATE_URL_TOKEN",
              "text": "🌌 Exact synthetic source evidence supports a conditional analogy.",
              "content_kind": "abstract",
              "retrieved_at": "2026-10-28T00:00:00Z",
              "extraction_method": "private-extractor-v1"
            }
          ],
          "events": [
            {
              "sequence": 1,
              "kind": "private-event",
              "summary": "PRIVATE_EVENT_CONTEXT"
            }
          ],
          "usage": {
            "model_calls": 12,
            "input_tokens_reported": 20,
            "output_tokens_reported": 40,
            "calls_without_usage": 0
          },
          "error_code": null,
          "validation_scope": "source_identity_and_quote_integrity"
        },
        "parent_edges": [],
        "primary_paper_groups": [
          {
            "paper_id": "paper-1",
            "source_ids": [
              "private-source-a"
            ]
          },
          {
            "paper_id": "paper-2",
            "source_ids": [
              "private-source-b"
            ]
          }
        ],
        "status": "current_supported"
      }
    ]
  },
  "rankings": {
    "run_id": "00000000-0000-0000-0000-000000000384",
    "rankings": [
      {
        "ranked": [
          {
            "reference": {
              "result_id": "00000000-0000-0000-0000-0000000000c9",
              "run_id": "00000000-0000-0000-0000-000000000384",
              "content_sha256": "0000000000000000000000000000000000000000000000000000000000000002"
            },
            "rationale": "The model prefers this weaker-scored but more useful question."
          },
          {
            "reference": {
              "result_id": "00000000-0000-0000-0000-0000000000c8",
              "run_id": "00000000-0000-0000-0000-000000000384",
              "content_sha256": "0000000000000000000000000000000000000000000000000000000000000001"
            },
            "rationale": "The higher confidence score does not determine ranking."
          }
        ],
        "excluded": [],
        "ranking_id": "00000000-0000-0000-0000-0000000003e8",
        "run_id": "00000000-0000-0000-0000-000000000384",
        "packet_sha256": "49b9afb0e8a2a4f4e20107aaf55e95bf7287f6a7715a0f329c5e869c42c061a1",
        "model_request_id": "model-0010",
        "action_ordinal": 10,
        "covered_through_ordinal": 1
      },
      {
        "ranked": [
          {
            "reference": {
              "result_id": "00000000-0000-0000-0000-0000000000ca",
              "run_id": "00000000-0000-0000-0000-000000000384",
              "content_sha256": "0000000000000000000000000000000000000000000000000000000000000003"
            },
            "rationale": "Model preference: the lowest-scored original bridge opens the most useful testable question."
          },
          {
            "reference": {
              "result_id": "00000000-0000-0000-0000-0000000000c9",
              "run_id": "00000000-0000-0000-0000-000000000384",
              "content_sha256": "0000000000000000000000000000000000000000000000000000000000000002"
            },
            "rationale": "Model preference: another useful direction, independently ordered."
          }
        ],
        "excluded": [
          {
            "reference": {
              "result_id": "00000000-0000-0000-0000-0000000000c8",
              "run_id": "00000000-0000-0000-0000-000000000384",
              "content_sha256": "0000000000000000000000000000000000000000000000000000000000000001"
            },
            "reason": "The model excluded this higher-scored mapping from its final recommendation; the evidence remains saved."
          }
        ],
        "ranking_id": "00000000-0000-0000-0000-0000000003e9",
        "run_id": "00000000-0000-0000-0000-000000000384",
        "packet_sha256": "ffc16193db767c310caf225258e0238d78a4564924129f9a7f16e00f346a7de3",
        "model_request_id": "model-0016",
        "action_ordinal": 16,
        "covered_through_ordinal": 2
      }
    ]
  },
  "empty_ranking": {
    "ranked": [],
    "excluded": [],
    "ranking_id": "00000000-0000-0000-0000-0000000003e8",
    "run_id": "00000000-0000-0000-0000-000000000384",
    "packet_sha256": "3e811d1946164bd2a47bd6976b317ab96375d5234f425ecff30a31eb4af6538d",
    "model_request_id": "model-0001",
    "action_ordinal": 1,
    "covered_through_ordinal": null
  },
  "profile": {
    "profile": {
      "task_kind": "research",
      "controller_protocol": "polymath-meta-explore-collection-v1",
      "controller_sha256": "819e1150aa4e9560e1dd02498959817b6f4707d9e92b3f209e8e8181da531591",
      "request_protocol": "polymath-meta-research-request-v1",
      "max_model_calls": 12,
      "max_actions": 16,
      "max_repairs": 2,
      "max_output_tokens": 4096,
      "max_context_characters": 90000,
      "request_timeout_seconds": 45.0,
      "model_timeout_seconds": 75.0,
      "tool_timeout_seconds": 20.0,
      "max_sources": 200,
      "collection_protocol": "polymath-explore-collection-v1",
      "max_bridges": 4
    },
    "profile_sha256": "320757c66a89904cd0fa502e3cf3c8f4e2e97f6ca31479914e867946a39de702"
  },
  "ordinary_profile": {
    "task_kind": "research",
    "controller_protocol": "polymath-meta-research-controller-v1",
    "controller_sha256": "1d596707f24f6786cefba5faf7b4eb6dbacee7a945727bac0be5b6e7bf118a56",
    "request_protocol": "polymath-meta-research-request-v1",
    "max_model_calls": 12,
    "max_actions": 16,
    "max_repairs": 2,
    "max_output_tokens": 4096,
    "max_context_characters": 90000,
    "request_timeout_seconds": 45.0,
    "model_timeout_seconds": 75.0,
    "tool_timeout_seconds": 20.0,
    "max_sources": 200
  }
}
