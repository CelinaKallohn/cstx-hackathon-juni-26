import { Component, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import {Spotprice} from './spotprice/spotprice.component';
import { Datepicker } from './datepicker/datepicker.component';
import { Auslastung } from './auslastung/auslastung.component';
import { Ausgaben } from './ausgaben/ausgaben.component';
import { Gewinn } from './gewinn/gewinn.component';
import { Tagesgewinn } from './tagesgewinn/tagesgewinn.component';
import { DateSelectionService } from './services/date-selection.service';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [CommonModule, Spotprice, Datepicker, Auslastung, Ausgaben, Gewinn, Tagesgewinn],
  templateUrl: './app.html',
  styleUrl: './app.css'
})
export class App {
  protected readonly title = signal('hackathon-energy');

  constructor(readonly ds: DateSelectionService) {}
}
