from aiogram import Router

from . import wb_monitor, cmds

router = Router()
router.include_router(cmds.router)
router.include_router(wb_monitor.router)
