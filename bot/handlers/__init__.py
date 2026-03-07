from aiogram import Router

from . import (
    wb_monitor,
    cmds,
    compare,
    quick_item,
    tracks,
    find_cheaper,
    payment,
    admin,
    settings,
    support,
)

router = Router()
router.include_router(cmds.router)
router.include_router(wb_monitor.router)
router.include_router(compare.router)
router.include_router(quick_item.router)
router.include_router(tracks.router)
router.include_router(find_cheaper.router)
router.include_router(payment.router)
router.include_router(admin.router)
router.include_router(settings.router)
router.include_router(support.router)
