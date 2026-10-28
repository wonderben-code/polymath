/* Synthetic fixture captured from the actual pure Python manifest builder. No native calls. */
export const pythonScientificManifest = {
  "protocol": "polymath-meta-research-activation-v1",
  "audience": "operator_browser",
  "readiness_id": "12345678-1234-1234-1234-123456789abc",
  "proposal_sha256": "25e754dd43167328eb8cdfda83e0989c10343514420f49089d4b8a1645cf54f8",
  "proposal": {
    "protocol": "polymath-meta-readiness-v1",
    "model_id": "fixture-attested-model",
    "endpoint": "https://api.meta.ai/v1/chat/completions",
    "currency": "USD",
    "usage_contract": "prompt_completion_inclusive_v1",
    "observed_at": "2026-10-28T11:59:00Z",
    "valid_until": "2026-10-28T13:00:00Z",
    "billable_bounds": {
      "evidence": [
        {
          "url": "https://example.org/bounds",
          "content_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        }
      ],
      "input_tokens_absolute": 300000,
      "output_tokens_absolute": 4096
    },
    "pricing": {
      "evidence": [
        {
          "url": "https://example.org/pricing",
          "content_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        }
      ],
      "input_microdollars_per_million": 1000000,
      "output_microdollars_per_million": 2000000
    },
    "usage_evidence": [
      {
        "url": "https://example.org/protocol-and-inclusive-usage",
        "content_sha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
      }
    ],
    "application_limits": {
      "max_output_tokens": 4096,
      "max_context_characters": 90000,
      "max_model_calls": 12,
      "request_timeout_seconds": 45.0
    },
    "account": {
      "account_id": "fixture-account",
      "hard_cap_microdollars": 150000000,
      "daily_cap_microdollars": 25000000,
      "run_cap_microdollars": 5000000,
      "max_in_flight": 1
    },
    "native_status": "not_run",
    "execution_available": false
  },
  "account_id": "fixture-account",
  "model_id": "fixture-attested-model",
  "endpoint": "https://api.meta.ai/v1/chat/completions",
  "credential_binding_sha256": "298c814f046daf78770c6e844ead60cd8a8d1be0115232cac383762d5876a1ae",
  "canary": {
    "receipt_id": "12345678-1234-1234-1234-123456789def",
    "receipt_sha256": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
    "account_id": "fixture-account",
    "model_id": "fixture-attested-model",
    "credential_binding_sha256": "298c814f046daf78770c6e844ead60cd8a8d1be0115232cac383762d5876a1ae",
    "endpoint": "https://api.meta.ai/v1/chat/completions",
    "probe_protocol": "polymath-meta-probe-v1",
    "status": "passed"
  },
  "review": {
    "model_wire_usage_confirmed": true,
    "pricing_bounds_confirmed": true,
    "funding_cap_confirmed": true,
    "observed_at": "2026-10-28T11:59:00Z",
    "valid_until": "2026-10-28T13:00:00Z",
    "evidence": [
      {
        "url": "https://example.org/current-human-review",
        "content_sha256": "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
      }
    ]
  },
  "provider_account_attestation": {
    "provider_billing_account_id": "fixture-provider-billing-account",
    "model_id": "fixture-attested-model",
    "currency": "USD",
    "usage_contract": "prompt_completion_inclusive_v1",
    "observed_at": "2026-10-28T11:59:00Z",
    "valid_until": "2026-10-28T13:00:00Z",
    "available_credit_microdollars": 150000000,
    "remaining_provider_allowance_microdollars": 150000000,
    "provider_hard_cap_microdollars": 150000000,
    "evidence": [
      {
        "url": "https://example.org/provider-billing-dashboard",
        "content_sha256": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
      }
    ]
  },
  "native_control_epoch": 4,
  "clear_kill_switch": true,
  "task_profiles": [
    {
      "profile": {
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
      },
      "profile_sha256": "bd5e67f239b6877812e9b24dc0212f45b8f4d1b46f5524db294516632102db88"
    },
    {
      "profile": {
        "task_kind": "formalise",
        "controller_protocol": "polymath-meta-artifact-controller-v1",
        "controller_sha256": "4d54a08ef5446d11a649cea00fecde6fb1b8450c5f2d71070a34b4c9c8ac7a28",
        "request_protocol": "polymath-meta-research-request-v1",
        "max_model_calls": 12,
        "max_actions": 12,
        "max_repairs": 2,
        "max_output_tokens": 4096,
        "max_context_characters": 90000,
        "request_timeout_seconds": 45.0,
        "model_timeout_seconds": 75.0,
        "tool_timeout_seconds": 5.0,
        "max_sources": 200
      },
      "profile_sha256": "a983a2ae0fccfea9baa0bf1384c7af9453d6d736f440e1b4ade57011a4805821"
    },
    {
      "profile": {
        "task_kind": "write_summary",
        "controller_protocol": "polymath-meta-artifact-controller-v1",
        "controller_sha256": "1677478e453671ca7db26ac84b89d752366c3454a47f07b246a1d88e75cbe78e",
        "request_protocol": "polymath-meta-research-request-v1",
        "max_model_calls": 12,
        "max_actions": 12,
        "max_repairs": 2,
        "max_output_tokens": 4096,
        "max_context_characters": 90000,
        "request_timeout_seconds": 45.0,
        "model_timeout_seconds": 75.0,
        "tool_timeout_seconds": 5.0,
        "max_sources": 200
      },
      "profile_sha256": "2bc18a4141f4cf1b69c8780b5177df0d84166816a3bed535113d63cb851c6254"
    },
    {
      "profile": {
        "task_kind": "write_paper",
        "controller_protocol": "polymath-meta-artifact-controller-v1",
        "controller_sha256": "8f172bf33e8af22f9fab39afe87ab483932e24954961a350c92c6c15f1e1c9d0",
        "request_protocol": "polymath-meta-research-request-v1",
        "max_model_calls": 12,
        "max_actions": 12,
        "max_repairs": 2,
        "max_output_tokens": 4096,
        "max_context_characters": 90000,
        "request_timeout_seconds": 45.0,
        "model_timeout_seconds": 75.0,
        "tool_timeout_seconds": 5.0,
        "max_sources": 200
      },
      "profile_sha256": "f6b23f6b498931ea07d788883bc38857390195e36cedf2faa2b37e19d887c79b"
    },
    {
      "profile": {
        "task_kind": "reassess_finding",
        "controller_protocol": "polymath-meta-assessment-controller-v1",
        "controller_sha256": "d51caa176d0bf82eac3e0a36f59e131f90f07ed4dab1a9cba8a0c0314bf961ba",
        "request_protocol": "polymath-meta-research-request-v1",
        "max_model_calls": 12,
        "max_actions": 12,
        "max_repairs": 2,
        "max_output_tokens": 4096,
        "max_context_characters": 90000,
        "request_timeout_seconds": 45.0,
        "model_timeout_seconds": 75.0,
        "tool_timeout_seconds": 20.0,
        "max_sources": 200
      },
      "profile_sha256": "ec03e849666b76718357f0be5b565cdad86c009cd17e67b21dda35308acbc79d"
    }
  ],
  "scientific_context_profiles": {
    "protocol": "polymath-scientific-context-profiles-v1",
    "bindings": [
      {
        "capability": "artifact_scientific_context_v1",
        "mode": null,
        "profile": {
          "task_kind": "formalise",
          "controller_protocol": "polymath-meta-artifact-scientific-context-v1",
          "controller_sha256": "017af8e43a805fd1752ec973b5603dbf3c54b5d9123f1c404c3dbc873f6387bd",
          "request_protocol": "polymath-meta-research-request-v1",
          "max_model_calls": 12,
          "max_actions": 12,
          "max_repairs": 2,
          "max_output_tokens": 4096,
          "max_context_characters": 90000,
          "request_timeout_seconds": 45.0,
          "model_timeout_seconds": 75.0,
          "tool_timeout_seconds": 5.0,
          "max_sources": 200
        },
        "profile_sha256": "c259601f6f09666f668ed64495fd3ed03d417a1d4c4977152265300e3e12c7b5"
      },
      {
        "capability": "artifact_scientific_context_v1",
        "mode": null,
        "profile": {
          "task_kind": "write_summary",
          "controller_protocol": "polymath-meta-artifact-scientific-context-v1",
          "controller_sha256": "2d1312c37f535d3ed36b88c5fbc2a5b4e90ae0062d6b5d585536eb403cdca0c1",
          "request_protocol": "polymath-meta-research-request-v1",
          "max_model_calls": 12,
          "max_actions": 12,
          "max_repairs": 2,
          "max_output_tokens": 4096,
          "max_context_characters": 90000,
          "request_timeout_seconds": 45.0,
          "model_timeout_seconds": 75.0,
          "tool_timeout_seconds": 5.0,
          "max_sources": 200
        },
        "profile_sha256": "316286bf6f41dd5e4009a61928857790d0677d863f36c9fefaef1e70377b84e0"
      },
      {
        "capability": "artifact_scientific_context_v1",
        "mode": null,
        "profile": {
          "task_kind": "write_paper",
          "controller_protocol": "polymath-meta-artifact-scientific-context-v1",
          "controller_sha256": "e8ed86aa589652438d87ed3d1d1e6ba9331684c9cd0ce377973a2ecde539e171",
          "request_protocol": "polymath-meta-research-request-v1",
          "max_model_calls": 12,
          "max_actions": 12,
          "max_repairs": 2,
          "max_output_tokens": 4096,
          "max_context_characters": 90000,
          "request_timeout_seconds": 45.0,
          "model_timeout_seconds": 75.0,
          "tool_timeout_seconds": 5.0,
          "max_sources": 200
        },
        "profile_sha256": "69758d0b1a787ba3ff2081c13bbbe7a5b2e8c180c5038dd0d0bcc750bceadd01"
      },
      {
        "capability": "ask_claim_overview_v1",
        "mode": "ask",
        "profile": {
          "task_kind": "research",
          "controller_protocol": "polymath-meta-ask-claim-overview-v1",
          "controller_sha256": "14fffc84568b91c3cabaccd9d8b85f128a9e35ed064f1fd654c37c4021d03db3",
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
        },
        "profile_sha256": "356d90f675fcbf27810d79f9681bb0e60dcdb95d6e23b50f747fa76bfc0bc357"
      },
      {
        "capability": "ask_reassessment_overview_v1",
        "mode": "ask",
        "profile": {
          "task_kind": "reassess_finding",
          "controller_protocol": "polymath-meta-ask-reassessment-overview-v1",
          "controller_sha256": "8f7c6c6d9e0d4c409b65d94c25e297e7e005b22f721d001c59be737a3507004c",
          "request_protocol": "polymath-meta-research-request-v1",
          "max_model_calls": 12,
          "max_actions": 12,
          "max_repairs": 2,
          "max_output_tokens": 4096,
          "max_context_characters": 90000,
          "request_timeout_seconds": 45.0,
          "model_timeout_seconds": 75.0,
          "tool_timeout_seconds": 20.0,
          "max_sources": 200
        },
        "profile_sha256": "b3f809cf8db59a554c9b2ab60110ed9c94c5a5862cc48029423e048efb3d2521"
      }
    ]
  },
  "valid_until": "2026-10-28T12:20:00Z"
}
