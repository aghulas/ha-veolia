# Veolia Eau (pro) — Home Assistant integration

**Status: scaffolding — nothing usable yet.** Unofficial, not affiliated with Veolia. This is a new
integration (domain `veolia_eau`), separate from the community integration
[Jezza34000/homeassistant_veolia](https://github.com/Jezza34000/homeassistant_veolia) (MIT), whose
work on the eau.veolia.fr API it builds on; see `docs/decision.md` for why.

Goal: daily water consumption of Veolia smart meters (eau.veolia.fr) in Home Assistant's statistics and
Energy dashboard, for business accounts too, with leak detection based on the consumption of days when
the building is empty. Read-only. See `docs/`. License: MIT.

---

*FR* — Consommation d'eau Veolia (compteur télérelevé, eau.veolia.fr) dans Home Assistant, y compris
pour les comptes professionnels, avec détection de fuite par le talon des jours de fermeture.
