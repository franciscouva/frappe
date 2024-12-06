import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Final, cast

from typing_extensions import override

from frappe.config import ConfigHandler, ConfigType

# used to implement legacy code paths to keep the main path clean
_current_bench: "Bench"


class BenchNotScopedError(NotImplementedError):
	pass


class BenchSiteNotLoadedError(ValueError):
	pass


class PathLike:
	path: Path

	def __fspath__(self) -> str:
		return str(self.path)


class Serializable:
	def __json__(self) -> dict[str, Any]:  # type: ignore[no-any-explicit]
		return {k: v for k, v in self.__dict__.items()}  # type: ignore[no-any-expr]


class BenchPathResolver(PathLike, Serializable):
	def __init__(self, path: str | Path | None = None, parent_path: Path | None = None) -> None:
		path = (
			path
			or os.environ.get(f"FRAPPE_{self.__class__.__name__.upper()}_PATH")
			or (parent_path.joinpath(self.__class__.__name__.lower()) if parent_path else None)
		)

		if not path and isinstance(self, Bench):
			path = (
				# TODO: legacy, remove
				os.environ.get("FRAPPE_BENCH_ROOT")
				or
				# TODO: unsave relative reference, remove:
				Path(__file__).resolve().parents[3]  # bench folder in standard layout
				# TODO: when above line removed, enable:
				# or (Path("~/frappe-bench").expanduser())
			)

		if path is None:
			raise ValueError(f"Unable to determine path for {self.__class__.__name__}")

		self.path = Path(path)


class FrappeComponent:
	python_path: Path
	name: str
	app: "Apps.App"

	def __bool__(self) -> bool:
		return (
			self._is_frappe_component()
			or (isinstance(self, Apps.App) and self._is_app_installed())
			or (isinstance(self, Apps.App.Module) and self._is_module_registered())
		)

	def _is_frappe_component(self) -> bool:
		return self.python_path.is_dir() and self.python_path.joinpath(".frappe").exists()

	def _is_app_installed(self) -> bool:
		global _current_bench
		res = self.name in _current_bench.sites.path.joinpath("apps.txt").read_text()
		if res:
			from frappe.deprecation_dumpster import deprecation_warning

			deprecation_warning(
				"2024-10-18",
				"yet unknown",
				f"Instead of adding {self.name} to sites/apps.txt, drop an empty file at {self.python_path}/.frappe",
			)
			return res
		return False

	def _is_module_registered(self) -> bool:
		res = self.name.title() in self.app.python_path.joinpath("modules.txt").read_text()
		if res:
			from frappe.deprecation_dumpster import deprecation_warning

			deprecation_warning(
				"2024-10-18",
				"yet unknown",
				f"Instead of adding {self.name.title()} to {self.app.python_path}/modules.txt, drop an empty file at {self.python_path}/.frappe",
			)
			return res
		return False


class Apps(BenchPathResolver):
	__apps: dict[str, "Apps.App"]

	class App(FrappeComponent, PathLike, Serializable):
		__modules: dict[str, "Apps.App.Module"]

		class Module(FrappeComponent, PathLike, Serializable):
			def __init__(self, *, name: str, path: Path, app: "Apps.App") -> None:
				self.app = app
				self.name = name
				self.path = path
				self.python_path = self.path

			@override
			def __repr__(self) -> str:
				return (
					super().__repr__()
					+ ("\n * Path: " + str(self.path))
					+ ("\n * Python Path: " + str(self.python_path))
				)

			@override
			def __json__(self) -> dict[str, Any]:  # type: ignore[no-any-explicit,no-any-decorated]
				excluded = (
					"app",  # prevent circular deps
				)
				return {k: v for k, v in self.__dict__.items() if k not in excluded}  # type: ignore[no-any-expr]

		def __init__(self, *, name: str, path: Path):
			self.name = name
			self.path = path
			self.python_path = self.path.joinpath(self.name)

		@override
		def __repr__(self) -> str:
			return (
				super().__repr__()
				+ ("\n * Path: " + str(self.path))
				+ ("\n * Python Path: " + str(self.python_path))
				+ ("\n * Modules:\n\t" + ("\n\t".join([str(module) for module in self.modules]) or "n/a"))
			)

		@override
		def __str__(self) -> str:
			return self.name

		@override
		def __json__(self) -> dict[str, Module]:
			return self.modules

		@property
		def modules(self) -> dict[str, Module]:
			if not hasattr(self, "__modules"):
				self.__modules = {
					d.name: module
					for d in self.python_path.iterdir()
					if d.is_dir() and (module := self.Module(name=d.name, path=d, app=self))
				}
			return self.__modules

		def __iter__(self) -> Iterator[Module]:
			return iter(self.modules.values())

		def __len__(self) -> int:
			return len(self.modules)

		def __getitem__(self, key: str) -> Module:
			return self.modules[key]

	def __init__(self, path: str | Path | None = None, parent_path: Path | None = None) -> None:
		super().__init__(path, parent_path)

	@override
	def __repr__(self) -> str:
		return (
			super().__repr__()
			+ ("\n * Apps Path: " + str(self.path))
			+ ("\n * Loaded Apps:\n\t" + ("\n\t".join([str(app) for app in self.apps]) or "n/a"))
		)

	@override
	def __json__(self) -> dict[str, App]:
		return self.apps

	@property
	def apps(self) -> dict[str, App]:
		if not hasattr(self, "__apps"):
			self.__apps = {
				d.name: app
				for d in self.path.iterdir()
				if d.is_dir() and (app := self.App(name=d.name, path=d))
			}
		return self.__apps

	def __iter__(self) -> Iterator[App]:
		return iter(self.apps.values())

	def __len__(self) -> int:
		return len(self.apps)

	def __getitem__(self, key: str) -> App:
		return self.apps[key]


class Logs(BenchPathResolver):
	def __init__(self, *, path: str | Path | None = None, parent_path: Path | None = None):
		super().__init__(path, parent_path)

	def get_log_file(self, log_type: str) -> Path:
		return self.path.joinpath(f"{log_type}.log")


class Run(BenchPathResolver):
	def __init__(self, *, path: str | Path | None = None, parent_path: Path | None = None):
		super().__init__(
			path
			# config is the legacy naming of this folder; a misnomer
			or (parent_path.joinpath("config") if parent_path else None),
			parent_path,
		)


class Sites(BenchPathResolver, ConfigHandler):
	SCOPE_ALL_SITES: Final = "__scope-all-sites__"
	__sites: dict[str, "Sites.Site"]

	class Site(ConfigHandler, PathLike, Serializable):
		_combined_config: ConfigType

		def __init__(self, *, bench: "Bench", name: str, path: str | Path):
			self.name = name
			self.path = Path(path)
			self.bench: Bench = bench
			ConfigHandler.__init__(self, self.path.joinpath("site_config.json"))

		@override
		def __repr__(self) -> str:
			return (
				super().__repr__() + ("\n * Site Path: " + str(self.path)) + ("\n * Site Name: " + self.name)
			)

		@override
		def __eq__(self, o: Any) -> bool:  # type: ignore[no-any-explicit,no-any-decorated]
			return self.name == str(o)  # type: ignore[no-any-expr]

		@override
		def __hash__(self) -> int:
			return hash(self.name)

		@override
		def __str__(self) -> str:
			return self.name

		@override
		def __json__(self) -> dict[str, Any]:  # type: ignore[no-any-explicit,no-any-decorated]
			excluded = (
				"bench",  # prevent circular deps
				"_ConfigHandler__config",  # holds file contents
				"_config_stale",  # never stale after accessored
				"_config",
			)
			naming = {"_combined_config": "config"}
			self.config  # ensure config is loaded
			return {naming.get(k, k): v for k, v in self.__dict__.items() if k not in excluded}  # type: ignore[no-any-expr]

		@property
		@override
		def config(self) -> ConfigType:
			if not hasattr(self, "_combined_config") or self._config_stale or self.bench.sites._config_stale:
				site_config = super().config.copy()
				config = self.bench.sites.config.copy()
				config.update(site_config)
				self._combined_config = ConfigType(**config)
			return self._combined_config

	def __init__(self, *, bench: "Bench", path: str | Path | None = None, parent_path: Path | None = None):
		BenchPathResolver.__init__(self, path, parent_path)
		ConfigHandler.__init__(self, self.path.joinpath("common_site_config.json"))
		self.bench = bench
		# security & thread-safety: site_name is stored in thread-local storage
		self.site_name = os.environ.get("FRAPPE_SITE") or cast(str | None, self.config.get("default_site"))

	@override
	def __repr__(self) -> str:
		return (
			super().__repr__()
			+ ("\n * Sites Path: " + str(self.path))
			+ ("\n * Scoped Site: " + (self.site_name or "n/a"))
			+ ("\n * Loaded Sites:\n\t" + ("\n\t".join([str(site) for site in self]) or "n/a"))
		)

	@override
	def __json__(self) -> dict[str, Any]:  # type: ignore[no-any-explicit,no-any-decorated]
		excluded = (
			"bench",  # prevent circular deps
			"_ConfigHandler__config",  # holds file contents
			"_config_stale",  # never stale after accessored
		)
		naming = {"_config": "config", "_Sites__sites": "_sites"}
		self._sites  # ensure sites are loaded
		return {naming.get(k, k): v for k, v in self.__dict__.items() if k not in excluded}  # type: ignore[no-any-expr]

	@property
	def site_name(self) -> str | None:
		import frappe

		return cast(str | None, getattr(frappe.local, "site_name", None))

	@site_name.setter
	def site_name(self, value):
		import frappe

		frappe.local.site_name = value

	def add_site(self, site_name: str) -> None:
		site_path = self.path.joinpath(site_name)
		for dir_path in [
			site_path.joinpath("public", "files"),
			site_path.joinpath("private", "backups"),
			site_path.joinpath("private", "files"),
			site_path.joinpath("locks"),
			site_path.joinpath("logs"),
		]:
			dir_path.mkdir(parents=True, exist_ok=True)
		self.__sites[site_name] = self.Site(bench=self.bench, name=site_name, path=site_path)

	def remove_site(self, site_name: str) -> None:
		if site_name in self.__sites:
			del self.__sites[site_name]
			# site_path = self.path.joinpath(site_name)
			# Note: This doesn't actually delete the site directory, just removes it from the sites dict
			# Actual deletion should be handled separately with proper safeguards

	def scope(self, site_name: str | None = None) -> "Sites.Site":
		if site_name is None:
			return self.site

		del self.__sites  # trigger re-scan after scope
		self.site_name = site_name

		if self.site_name != self.SCOPE_ALL_SITES:
			return self.site

		raise BenchNotScopedError("Cannot scope to ALL_SITES")

	@property
	def scoped(self) -> bool:
		return bool(self.site_name) and self.site_name != self.SCOPE_ALL_SITES

	@property
	def site(self) -> Site:
		# security & thread-safety: site_name is stored in thread-local storage
		if not self.site_name or self.site_name == self.SCOPE_ALL_SITES:
			raise BenchNotScopedError("Sites was not scoped to a single site, yet.")
		return self[self.site_name]

	@property
	def _sites(self) -> dict[str, Site]:
		if not hasattr(self, "__sites"):
			self.__sites = {}

			def _process(path: Path):
				if path.is_dir() and path.joinpath("site_config.json").exists():
					self.__sites[path.name] = self.Site(bench=self.bench, name=path.name, path=path)

			# security & thread-safety: site_name is stored in thread-local storage
			if self.site_name and self.site_name != self.SCOPE_ALL_SITES:
				_process(self.path.joinpath(self.site_name))
			elif self.site_name == self.SCOPE_ALL_SITES:
				for site_path in self.path.iterdir():
					_process(site_path)
		return self.__sites

	def __iter__(self) -> Iterator[Site]:
		# security & thread-safety: site_name is stored in thread-local storage
		if self.site_name == self.SCOPE_ALL_SITES:
			return iter(self._sites.values())
		elif self.site_name:
			return iter([self[self.site_name]])
		raise BenchNotScopedError("Sites was not scoped, yet.")

	def __len__(self) -> int:
		return len(self._sites)

	def __getitem__(self, key: str) -> Site:
		try:
			return self._sites[key]
		except KeyError:
			raise BenchSiteNotLoadedError(f"Site '{key}' was not loaded")


class Bench(BenchPathResolver):
	def __init__(self, path: str | Path | None = None):
		super().__init__(path)
		self.logs = Logs(parent_path=self.path)
		self.run = Run(parent_path=self.path)
		self.sites = Sites(bench=self, parent_path=self.path)
		global _current_bench
		_current_bench = self
		self.apps = Apps(parent_path=self.path)

	def scope(self, site_name: str) -> Sites.Site:
		return self.sites.scope(site_name)

	@property
	def scoped(self) -> bool:
		return self.sites.scoped

	@override
	def __repr__(self) -> str:
		return (
			super().__repr__()
			+ ("\n * Bench Path: " + str(self.path))
			+ ("\n\n" + str(self.apps))
			+ ("\n\n" + str(self.sites))
		)
