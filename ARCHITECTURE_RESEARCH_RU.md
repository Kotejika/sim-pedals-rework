# Архитектурное исследование похожих проектов

Дата проверки: 11 ноября 2026.

## Что сравнивалось

| Проект | Что полезно для Sim Pedals | Что не переносить вслепую |
|---|---|---|
| [FreeJoy](https://github.com/FreeJoy-Team/FreeJoy) + [Configurator](https://github.com/FreeJoy-Team/FreeJoyConfiguratorQt) | Раздельная настройка осей: калибровка, smoothing, inversion, deadband, curve; отдельная Qt-конфигурационная программа; сохранение/загрузка настроек | Это STM32F103-прошивка, другой MCU и другая аппаратная архитектура |
| [OpenFFBoard](https://github.com/Ultrawipf/OpenFFBoard) | Чёткое разделение firmware / configurator, версии GUI должны соответствовать версии firmware; HID и независимая host-конфигурация | Проект ориентирован на FFB-рули/joysticks, не на наш ADS1220 + ESB wireless pedal node |
| [OpenSimPedals](https://github.com/manoukianv/OpenSimPedals) | Standalone USB HID, автоматическая калибровка, хранение настроек, явное отображение диапазона датчика | Иная электроника (ADS1232 и ATmega32U4); не копировать их частоты/драйвер |
| [CNCDan / Sim Pedals](https://github.com/dmcke5/simpedals) | Поддержка load cell и Hall sensors в механике педалей; NA151 200kg load cell фигурирует в BOM; калибровка полного диапазона | Автор прямо помечает калибровочную часть ПО как недостаточно проверенную на реальном железе |
| [DIY Sim Racing FFB Pedal](https://github.com/chrgri/diy-sim-racing-ffb-pedal) | Хорошо разделены механика, электроника, control board и software; профили и телеметрия как отдельные задачи | Это активная силовая педаль с сервоприводом, потенциально опасной механикой; наша прошивка не должна заимствовать управляющие алгоритмы |

## Выводы, применённые в Sim Pedals

1. **Три реальные оси остаются тремя осями.** ADS1220 diagnostics передаются отдельным metadata extension, а не притворяются дополнительными gamepad axes.
2. **GUI и firmware — один согласованный протокол.** Python parser повторяет ограничения C parser; сборка тестирует их вместе.
3. **Настройки остаются host-configurable.** Калибровка, smoothing, deadzone, inversion, curve, tare/scale и profiles принадлежат отдельным axes; диагностическая информация не изменяет HID mapping.
4. **Драйвер не скрывает ошибки.** Все GPIO return codes проверяются; при ошибке bus приводится в idle, а диагностический код и регистры доступны в GUI.
5. **Симуляция не считается тестом железа.** Host test проверяет порядок фронтов и обработку ошибок, но не доказывает физический сигнал на выводах ADS1220.
6. **Скорость и latency нужно измерять end-to-end.** 1000 SPS в конфигурации АЦП не доказывают 1000 обновлений HID в секунду; важны время преобразования, ISR, чтение SPI, ESB, Hub, USB и jitter.

## Лицензии и заимствование

Это сравнительный обзор, а не перенос исходников. FreeJoy и OpenSimPedals опубликованы под GPL-3.0, OpenFFBoard — MIT, другие проекты имеют собственные лицензии. В Sim Pedals заимствуются архитектурные идеи и открытые практики; чужие исходники не копируются без проверки совместимости лицензий.

## Источники

- https://github.com/FreeJoy-Team/FreeJoy
- https://github.com/FreeJoy-Team/FreeJoyConfiguratorQt
- https://github.com/Ultrawipf/OpenFFBoard
- https://github.com/manoukianv/OpenSimPedals
- https://github.com/dmcke5/simpedals
- https://github.com/chrgri/diy-sim-racing-ffb-pedal
