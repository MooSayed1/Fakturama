# Samples

- `order.png`: the synthetic sales order from the assessment brief (order `WEB-2026-0714-A17`), at its original size of 1489×2048. It's the input for the automation.
- `order.expected.json`: the values a human reads from that image. It's used to test the Fakturama flow without calling the LLM (`--json samples/order.expected.json --from-json`), and to compare against what the LLM extracts.
