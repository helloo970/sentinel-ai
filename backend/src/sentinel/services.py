"""Service container: holds all building blocks together and wires them up."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sentinel.audit import AuditLog
from sentinel.config import Settings
from sentinel.db import Database, SettingsStore
from sentinel.events import EventBus


@dataclass
class Services:
    config: Settings
    db: Database
    store: SettingsStore
    audit: AuditLog
    bus: EventBus
    # The following fields are set in build_services()
    registry: Any = None
    engine: Any = None
    programs: Any = None
    notifier: Any = None
    memory: Any = None
    llm: Any = None
    costs: Any = None
    chat: Any = None
    monitor: Any = None
    alerts: Any = None
    network: Any = None
    reminders: Any = None
    routines: Any = None
    server: Any = None
    news: Any = None
    voice: Any = None
    agents: Any = None
    plugins: Any = None
    pin: Any = None
    responses: Any = None
    tour: Any = None
    shop: Any = None
    geoip: Any = None
    oui: Any = None
    certs: Any = None
    weather: Any = None
    mail: Any = None
    reports: Any = None
    filescan: Any = None
    modes: Any = None
    scheduler: Any = None
    relay: Any = None
    prices: Any = None
    shopping: Any = None
    phone: Any = None
    edge_voice: Any = None
    eleven: Any = None
    brain: Any = None
    markets: Any = None


def build_services(config: Settings, *, llm: Any = None) -> Services:
    """Create and wire all services. `llm` can be replaced in tests."""
    from sentinel.actions import register_all
    from sentinel.agents.service import AgentService
    from sentinel.automation.modes import ModeService
    from sentinel.automation.programs import ProgramCatalog
    from sentinel.automation.reminders import ReminderService
    from sentinel.automation.routines import RoutineCatalog
    from sentinel.automation.scheduler import AutomationScheduler
    from sentinel.brain.service import BrainService
    from sentinel.chat.service import ChatService
    from sentinel.llm.costs import CostTracker
    from sentinel.llm.factory import create_provider
    from sentinel.mail.service import GmailService
    from sentinel.markets.service import MarketService
    from sentinel.memory.store import MemoryStore
    from sentinel.monitor.alerts import AlertStore
    from sentinel.monitor.certs import CertWatch
    from sentinel.monitor.service import MonitorService
    from sentinel.network.inventory import NetworkInventory
    from sentinel.network.lookup import GeoIP, OuiVendors
    from sentinel.news.briefing import NewsService
    from sentinel.news.tour import NewsTour
    from sentinel.notify.service import Notifier
    from sentinel.pcsec.filescan import FileScanner
    from sentinel.permissions import ActionRegistry, PermissionEngine
    from sentinel.phone.link import PhoneLink
    from sentinel.pin import PinStore
    from sentinel.plugins.loader import PluginManager
    from sentinel.reports.service import ReportService
    from sentinel.response.registry import PluginRegistry
    from sentinel.server_agent.client import ServerAgentClient
    from sentinel.server_agent.relay import RelayService
    from sentinel.shop.prices import PriceWatcher
    from sentinel.shop.service import ShopService
    from sentinel.shop.shopping import ShoppingList
    from sentinel.voice.edge_tts_engine import EdgeVoice
    from sentinel.voice.elevenlabs import ElevenLabsVoice
    from sentinel.voice.service import VoiceService
    from sentinel.weather.service import WeatherService

    config.data_dir.mkdir(parents=True, exist_ok=True)
    config.config_dir.mkdir(parents=True, exist_ok=True)
    db = Database(config.db_path)
    bus = EventBus()
    svc = Services(config=config, db=db, store=SettingsStore(db), audit=AuditLog(db), bus=bus)

    svc.registry = ActionRegistry()
    svc.engine = PermissionEngine(
        registry=svc.registry,
        db=db,
        audit=svc.audit,
        store=svc.store,
        services=svc,
        default_autonomy=config.autonomy_default,
        max_autonomous_per_hour=config.max_autonomous_per_hour,
        undo_window_s=config.undo_window_s,
        approval_ttl_s=config.approval_ttl_s,
        on_event=bus.publish,
    )
    svc.notifier = Notifier(svc)
    svc.plugins = PluginManager(svc)
    svc.programs = ProgramCatalog(config.config_dir)
    svc.routines = RoutineCatalog(config.config_dir, plugins=svc.plugins)
    svc.memory = MemoryStore(db)
    svc.costs = CostTracker(db, config)
    svc.llm = llm if llm is not None else create_provider(config)
    svc.alerts = AlertStore(svc)
    svc.filescan = FileScanner(svc)
    geo_dir = config.data_dir / "geo"
    svc.geoip = GeoIP(geo_dir / "dbip-country-lite.csv")
    svc.oui = OuiVendors(geo_dir / "oui.csv")
    svc.network = NetworkInventory(svc)
    svc.certs = CertWatch(svc)
    svc.reminders = ReminderService(svc)
    svc.modes = ModeService(svc)
    svc.scheduler = AutomationScheduler(svc)
    svc.server = ServerAgentClient(config)
    svc.relay = RelayService(svc)
    svc.phone = PhoneLink(svc)
    svc.news = NewsService(svc)
    svc.tour = NewsTour(svc)
    svc.shop = ShopService(svc)
    svc.prices = PriceWatcher(svc)
    svc.shopping = ShoppingList(svc)
    svc.weather = WeatherService(svc)
    svc.mail = GmailService(svc)
    svc.reports = ReportService(svc)
    svc.brain = BrainService(svc)
    svc.markets = MarketService(svc)
    svc.chat = ChatService(svc)
    svc.monitor = MonitorService(svc)
    svc.voice = VoiceService(svc)
    svc.edge_voice = EdgeVoice(config.tts_voice)
    svc.eleven = ElevenLabsVoice(config.config_dir)
    svc.agents = AgentService(svc)
    svc.pin = PinStore(svc.store, svc.audit)
    register_all(svc.registry)
    svc.responses = PluginRegistry(svc)
    svc.responses.reload()
    return svc
