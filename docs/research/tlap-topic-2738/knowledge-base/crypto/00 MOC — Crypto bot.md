---
title: "Криптовалютный бот — MOC"
tags:
  - crypto/grid
  - trading/portfolio
  - status/research
---

# Криптовалютный бот — MOC

Назад: [[../00 MOC — Setka research]]

Эта ветка отделяет переносимые инженерные идеи от форексных значений и форумных заявлений. В самой теме есть отдельные сообщения об ETHUSD, Bybit, BTC/ETH, фьючерсах и USDT, но нет полной независимой методики крипто-сеток. Поэтому все пункты ниже требуют проверки на выбранной бирже и инструменте.

## Состав ветки

- [План развития Bitget BTC → портфель](../../../../plans/crypto_grid_bot_development_plan_2026-08-17.md)
- [Ревью реализации и рекомендации следующим моделям](../../../../plans/crypto_grid_bot_implementation_review_2026-08-17.md)
- [Состояние реализации и machine gates](../../../../../analysis_reports/crypto_bot_roadmap/README.md)
- [Исторический протокол отбора BTC/ETH](../../../../../analysis_reports/crypto_bot_roadmap/historical_protocol/2026-08-17/report.md)
- [[01 Transfer matrix]] — что можно перенести, а что нужно заменить.
- [[02 Grid specification]] — точная спецификация одной сетки.
- [[03 Risk controls]] — риск сетки, портфеля и ликвидации.
- [[04 Backtest and walk-forward]] — история, исполнение и форвард.
- [[05 Metrics and monitoring]] — показатели и рабочий мониторинг.
- [[06 Research backlog]] — открытые вопросы перед оптимизацией.

## Форумные исходные наблюдения

- ETHUSD и торговля в выходные на Bybit: [пост #17675](https://tlap.club/topic/2738-sovetnik-forex-setka-trader-mod-i-ea-setka/?do=findComment&comment=510571).
- MT5 на Bybit: [пост #19645](https://tlap.club/topic/2738-sovetnik-forex-setka-trader-mod-i-ea-setka/?do=findComment&comment=546221).
- BTC и ETH на резком новостном движении: [пост #19583](https://tlap.club/topic/2738-sovetnik-forex-setka-trader-mod-i-ea-setka/?do=findComment&comment=544680).
- Платежи USDT и риски биржевых адресов: [пост #19563](https://tlap.club/topic/2738-sovetnik-forex-setka-trader-mod-i-ea-setka/?do=findComment&comment=544604).

Это наблюдения участников, а не проверка стратегии или условий биржи.
