from discord.ui import Button
import discord

class DuckGameView(...):
    def __init__(self, ..., start_position=-1):
        ...
        self.position = start_position
        # Controls: only Forward on grass (-1); add Stop once on first lane or beyond
        self.forward_button = Button(label="Forward", style=discord.ButtonStyle.success)
        self.forward_button.callback = self.forward_button_callback
        self.add_item(self.forward_button)

        self.stop_button = Button(label="Stop", style=discord.ButtonStyle.danger)
        self.stop_button.callback = self.stop_button_callback
        if self.position >= 0:
            self.add_item(self.stop_button)
        ...

    async def forward_button_callback(self, interaction):
        await _ack(interaction)
        # existing owner check and other code...

        # Reached/passed finish: position index is total_lanes (grass=-1, lanes 0..N-1, finish=N)
        if self.position >= self.total_lanes:
            if self.multipliers:
                self.multiplier = float(self.multipliers[-1])  # final multiplier
            # winnings are based on the original stake (amount), not bank
            self.session_wallet = float(self.amount) * self.multiplier

        # existing code continues...

        # In safe-move branch, where new view is created:
        new_view = DuckGameView(..., start_position=self.position)
        # existing code continues...